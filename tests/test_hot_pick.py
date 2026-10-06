import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from contracts import validate
from pipeline import hot_pick as freezer
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.marts import briefing
from pipeline.marts import hot_pick as m
from pipeline.sources import hot_pick as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}
CFG = m.config()

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Anthropic opens an office in Seoul - Reuters</title><link>https://news.google.com/rss/articles/a</link>
<pubDate>Mon, 05 Oct 2026 20:01:09 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
<item><title>No outlet on this one</title><link>https://news.google.com/rss/articles/b</link><pubDate>Mon, 05 Oct 2026 20:01:09 GMT</pubDate></item>
<item><title>No date either - BBC</title><link>https://news.google.com/rss/articles/c</link><source url="https://bbc.com">BBC</source></item>
</channel></rss>"""


# ---- the collectors' parsers ----


def test_the_feed_parser_keeps_complete_items_strips_the_outlet_from_the_title_and_dates_in_utc():
    [row] = list(src.parse_news(RSS, "anthropic"))
    assert row["dims"] == {
        "title": "Anthropic opens an office in Seoul",
        "outlet": "Reuters",
        "published": "2026-10-05T20:01:09Z",
    }
    assert row["as_of"] == "2026-10-05" and row["metric"] == "news_headline" and row["source_url"].endswith("/a")
    validate(
        "observation",
        {**row, "source": "news_headlines", "method": "api", "tier": "press", "retrieved_at": "2026-10-06T08:00:00Z"},
    )


def test_the_story_parser_values_a_story_at_its_points_and_skips_ones_without():
    payload = {
        "hits": [
            {
                "objectID": "7",
                "title": "A story",
                "points": 120,
                "num_comments": 33,
                "url": "https://x.example",
                "created_at": "2026-10-05T10:00:00Z",
            },
            {"objectID": "8", "title": "No points", "points": None, "created_at": "2026-10-05T10:00:00Z"},
            {"objectID": "9", "title": None, "points": 5, "created_at": "2026-10-05T10:00:00Z"},
        ]
    }
    [row] = list(src.parse_stories(payload, "anthropic"))
    assert (
        row["value"] == 120
        and row["dims"]["comments"] == 33
        and row["source_url"] == "https://news.ycombinator.com/item?id=7"
    )
    validate(
        "observation",
        {**row, "source": "hn_stories", "method": "api", "tier": "platform", "retrieved_at": "2026-10-06T08:00:00Z"},
    )


def test_only_the_target_company_is_collected(monkeypatch):
    monkeypatch.setattr(src, "get", lambda *a, **k: pytest.fail("a peer must not be fetched"))
    assert list(src.news_headlines(load_companies(ROOT)["openai"])) == []
    assert list(src.hn_stories(load_companies(ROOT)["openai"])) == []


# ---- news: grouping and picking ----


def story(title, outlet, published="2026-10-05T10:00:00Z"):
    return {"title": title, "outlet": outlet, "published": published, "source_url": f"https://n.example/{outlet}"}


def news(*items):
    return pd.DataFrame(items)


def test_headlines_about_one_story_are_grouped_even_when_the_words_differ():
    assert m.tokens("Executives testifies at hearing") == m.tokens(
        "Executives testify at hearing"
    )  # testifies -> testify
    items = [
        story("OpenAI, Google testify at NYC hearing", "Reuters"),
        story("NYC council hearing: Google, OpenAI testify", "Bloomberg"),
        story("Anthropic opens an office in Seoul", "Yonhap"),
    ]
    groups = m.clusters(items, 0.3, 2)
    assert sorted(len(g) for g in groups) == [1, 2]


def test_a_story_needs_two_outlets_and_the_most_covered_come_first():
    df = news(
        story("Chip deal for Broadcom financed by banks", "A"),
        story("Banks finance chip deal for Broadcom", "B"),
        story("Banks finance Broadcom chip deal worth billions", "C"),
        story("Seoul office opens for the lab", "D"),
        story("Seoul office for the lab opens", "E"),
        story("A lone headline about quarterly pricing", "F"),
    )
    picks = m.news_picks(df, CFG["news"], 7)
    assert [p["why"].split(" outlets")[0] for p in picks] == ["3", "2"]  # the lone headline never qualifies


def test_a_story_that_mostly_repeats_one_already_chosen_is_skipped_and_places_are_limited():
    df = news(
        story("NYC council hearing hears OpenAI and Google testify", "A"),
        story("NYC council hearing: OpenAI, Google testify", "B"),
        story("Former researcher testifies as NYC council hearing weighs AI curbs", "C"),
        story("Researcher testifies at NYC council hearing on AI curbs", "D"),
    )
    assert len(m.news_picks(df, {**CFG["news"], "repeat_shared": 3}, 7)) == 1
    assert len(m.news_picks(df, {**CFG["news"], "repeat_shared": 99, "places": 1}, 7)) == 1


# ---- discussion, filings, insights ----


def test_discussion_picks_the_top_story_above_the_points_floor():
    df = pd.DataFrame(
        [
            {
                "title": "Big",
                "value": 721,
                "comments": 558,
                "created": "2026-10-05T09:00:00Z",
                "source_url": "https://hn/1",
            },
            {
                "title": "Bigger but under the floor",
                "value": 20,
                "comments": 2,
                "created": "2026-10-05T09:00:00Z",
                "source_url": "https://hn/2",
            },
        ]
    )
    [pick] = m.discussion_picks(df, CFG["discussion"])
    assert pick["headline"] == "Big" and pick["why"].startswith("721 points, 558 comments")
    assert m.discussion_picks(df.iloc[1:], CFG["discussion"]) == []


def test_filings_need_enough_distinct_filers_and_say_that_a_mention_is_not_a_purchase():
    df = pd.DataFrame(
        [
            {"cik": "1", "filer": "ACME CORP", "form": "10-Q", "as_of": "2026-10-03", "source_url": "https://sec/1"},
            {"cik": "2", "filer": "BETA INC", "form": "10-K", "as_of": "2026-10-04", "source_url": "https://sec/2"},
            {"cik": "2", "filer": "BETA INC", "form": "10-K", "as_of": "2026-10-02", "source_url": "https://sec/2b"},
        ]
    )
    [pick] = m.filing_picks(df, {"places": 1, "min_filers": 2})
    assert pick["why"].startswith("2 filers") and "Beta Inc (10-K)" in pick["why"] and "not a purchase" in pick["why"]
    assert pick["link"] == "https://sec/2" and m.filing_picks(df.iloc[:1], {"places": 1, "min_filers": 2}) == []


def read(sid, chg, prev=None, label=None):
    return {
        "id": sid,
        "label": label or sid,
        "chg": chg,
        "prev": prev,
        "text": "100",
        "based": "Jul to Sep 2026",
        "freq": "M",
    }


def test_insights_put_fired_tripwires_first_then_the_widest_moves_and_skip_flat_and_excluded_ones():
    cfg_b = {"f": {"page": "Page", "signals": {s: {"band": 0.10} for s in ("a", "b", "c", "d", "marks")}}}
    reads = {"f": [read("a", 0.25), read("b", 0.60, 0.1), read("c", 0.05), read("d", -0.50), read("marks", 1.27)]}
    rules = [{"id": "t", "label": "A fell", "signal": "d", "rule": {"type": "change_below", "value": -0.4}}]
    picks = m.insight_picks(cfg_b, reads, rules, {"places": 3, "min_bands": 2, "exclude": ["marks"]}, date(2026, 10, 6))
    assert [p["headline"].split(":")[0] for p in picks] == [
        "A fell",
        "b",
        "a",
    ]  # d fired, so it is not repeated as a mover
    assert picks[0]["why"].startswith("Tripwire fired") and picks[1]["why"].startswith("6.0× its flat band")
    assert "the period before it was +10%" in picks[1]["why"] and all(p["link"] is None for p in picks)


# ---- the chart ----


def obs(source, metric, as_of, value=1, retrieved="2026-10-06T08:00:00Z", **dims):
    return {
        "source": source,
        "source_url": f"https://x/{dims.get('title', as_of)}",
        "method": "api",
        "as_of": as_of,
        "retrieved_at": retrieved,
        "tier": "press",
        "entity": "anthropic",
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": None,
        "evidence": None,
    }


def story_rows(title, outlets, day="2026-10-05"):
    return [
        obs("news_headlines", "news_headline", day, title=title, outlet=o, published=f"{day}T10:00:00Z")
        for o in outlets
    ]


def test_the_window_ends_on_the_newest_collection_of_the_daily_feeds():
    ctx = Ctx(
        story_rows("x", ["A"])
        + [
            obs(
                "hn_stories",
                "hn_story_points",
                "2026-09-01",
                5,
                retrieved="2026-10-04T00:00:00Z",
                id="1",
                title="t",
                comments=0,
                url=None,
                created="2026-09-01T00:00:00Z",
            )
        ],
        NAMES,
    )
    assert m.window_end(ctx) == date(2026, 10, 6) and m.window_end(Ctx([], NAMES)) is None


def test_a_quiet_week_shows_fewer_picks_not_filler():
    rows = story_rows("Broadcom chip deal financed by banks", ["A", "B"]) + story_rows(
        "Banks finance Broadcom chip deal", ["C"]
    )
    spec = m.this_week(Ctx(rows, NAMES))
    kinds = [r["kind"] for r in spec["rows"]]
    assert kinds.count("News") == 1 and "Discussion" not in kinds and "Filings" not in kinds
    assert [r["rank"] for r in spec["rows"]] == list(range(1, len(kinds) + 1))
    assert spec["takeaway"][0].startswith(f"{len(kinds)} picks in the 7 days to 2026-10-06")


def test_headlines_older_than_the_window_are_not_picked():
    rows = story_rows("Old chip deal financed by banks", ["A", "B"], day="2026-09-20")
    assert [r for r in m.this_week(Ctx(rows, NAMES))["rows"] if r["kind"] == "News"] == []


def test_the_chart_is_awaiting_data_when_nothing_has_been_collected():
    registry.discover()
    spec = build_mart(
        registry.MARTS["hot_pick.this_week"], {sid: [] for sid in m.SOURCES}, datetime(2026, 10, 6, tzinfo=UTC), NAMES
    )
    assert spec["status"] == "awaiting_data" and spec["rows"] == []


def test_the_config_is_consistent():
    assert set(CFG["order"]) == {"news", "discussion", "insights", "filings"}
    assert sum(CFG[k]["places"] for k in CFG["order"]) <= 7
    briefing_ids = {s for f in briefing.config().values() for s in f["signals"]}
    assert set(CFG["insights"]["exclude"]) <= briefing_ids


# ---- freezing a week ----


@pytest.fixture
def root(tmp_path):
    for sub in ("config/companies", "config/identifiers", "config/metrics"):
        (tmp_path / sub).mkdir(parents=True)
    for f in (ROOT / "config/companies").glob("*.yaml"):
        (tmp_path / "config/companies" / f.name).write_text(f.read_text())
    for name in ("briefing.yaml", "tripwires.yaml", "hot_pick.yaml"):
        (tmp_path / "config" / name).write_text((ROOT / "config" / name).read_text())
    return tmp_path


def test_freezing_writes_a_valid_week_file_and_refuses_to_overwrite_a_past_week(root, monkeypatch):
    registry.discover()
    monkeypatch.setattr(
        freezer,
        "read_observations",
        lambda r, meta: (
            story_rows("Broadcom chip deal financed by banks", ["A", "B"]) if meta["id"] == "news_headlines" else []
        ),
    )
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(briefing, "ROOT", root)
    week = datetime(2026, 10, 7, 9, tzinfo=UTC)  # the same ISO week as the data
    out = freezer.freeze(root, now=week)
    spec = json.loads(out.read_text())
    assert (
        out.name == "hot_pick.week_2026_41.json"
        and spec["id"] == "hot_pick.week_2026_41"
        and spec["title"] == "Hot Pick, week 2026-W41"
    )
    validate("chart_spec", spec)
    assert freezer.freeze(root, now=week) == out  # the running week can be saved again
    with pytest.raises(SystemExit, match="never overwritten"):
        freezer.freeze(root, now=datetime(2026, 10, 14, tzinfo=UTC))  # a week later: that file is now a past week
