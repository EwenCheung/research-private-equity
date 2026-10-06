import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

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
NOW = datetime(2026, 10, 6, 9, tzinfo=UTC)

RSS = """<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Anthropic opens an office in Seoul</title><link>https://pub.example/a</link><pubDate>Mon, 05 Oct 2026 20:01:09 GMT</pubDate>
<description>&lt;p&gt;A short note&lt;/p&gt;</description></item>
<item><title>Spring openings at the museum</title><link>https://pub.example/b</link><pubDate>Mon, 05 Oct 2026 20:01:09 GMT</pubDate>
<description>A Claude Monet exhibition opens.</description></item>
<item><title>A roundup</title><link>https://pub.example/c</link><pubDate>Mon, 05 Oct 2026 20:01:09 GMT</pubDate>
<description>The new Claude model scores higher on coding tests.</description></item>
<item><title>Anthropic, long ago</title><link>https://pub.example/d</link><pubDate>Mon, 05 Jan 2026 20:01:09 GMT</pubDate></item>
<item><title>Anthropic with no date</title><link>https://pub.example/e</link></item>
</channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>llm-anthropic 0.30</title><link rel="alternate" href="https://blog.example/p"/><published>2026-10-05T10:00:00+00:00</published>
<summary>A plugin release.</summary></entry></feed>"""


# ---- the collectors' parsers ----


def test_a_headline_needs_anthropic_or_claude_beside_an_ai_word_not_a_first_name():
    assert src.mentions("Anthropic opens an office") and src.mentions("Claude Code now reads AGENTS.md")
    assert not src.mentions("Claude Monet exhibition opens") and not src.mentions("Claude Shannon turns 100")


def test_the_feed_parser_keeps_items_that_name_anthropic_in_the_title_or_summary_and_drops_the_rest():
    rows = list(src.parse_feed(RSS, "Example News", "news", "anthropic", NOW))
    assert [r["source_url"] for r in rows] == [
        "https://pub.example/a",
        "https://pub.example/c",
    ]  # not the museum, the old or the undated
    assert rows[0]["dims"] == {
        "title": "Anthropic opens an office in Seoul",
        "outlet": "Example News",
        "kind": "news",
        "published": "2026-10-05T20:01:09Z",
        "in_title": True,
    }
    assert (
        rows[1]["dims"]["in_title"] is False and "summary" not in rows[1]["dims"]
    )  # the summary decides, then is dropped
    validate(
        "observation",
        {**rows[0], "source": "ai_feeds", "method": "api", "tier": "press", "retrieved_at": "2026-10-06T08:00:00Z"},
    )


def test_the_feed_parser_reads_atom_links_and_dates():
    [row] = list(src.parse_feed(ATOM, "A Blog", "blog", "anthropic", NOW))
    assert (
        row["source_url"] == "https://blog.example/p" and row["as_of"] == "2026-10-05" and row["dims"]["kind"] == "blog"
    )


def test_a_feed_that_fails_is_skipped_and_named_but_every_feed_failing_is_an_error(monkeypatch, capsys):
    feeds = [
        {"name": "Broken", "url": "https://x/broken", "kind": "news"},
        {"name": "Fine", "url": "https://x/fine", "kind": "news"},
    ]
    company = SimpleNamespace(slug="anthropic", ids=lambda page: {"feeds": feeds})

    def fake_get(url, **kw):
        if "broken" in url:
            raise OSError("down")
        return SimpleNamespace(text=RSS)

    monkeypatch.setattr(src, "get", fake_get)
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    assert len(list(src.ai_feeds(company))) == 2 and "skipped Broken" in capsys.readouterr().err
    monkeypatch.setattr(src, "get", lambda url, **kw: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError, match="no feed"):
        list(src.ai_feeds(company))


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


def test_hacker_news_is_searched_for_each_word_and_a_story_found_twice_or_not_about_claude_the_model_is_dropped(
    monkeypatch,
):
    hit = lambda i, title: {
        "objectID": i,
        "title": title,
        "points": 100,
        "num_comments": 1,
        "created_at": "2026-10-05T10:00:00Z",
    }
    pages = {
        "Anthropic": [hit("1", "Anthropic news"), hit("2", "Claude Shannon at 100")],
        "Claude": [hit("1", "Anthropic news"), hit("3", "Claude Opus 5.5")],
    }
    seen = []

    def fake_get(url, params=None, **kw):
        seen.append(params["query"])
        return SimpleNamespace(json=lambda: {"hits": pages[params["query"]]})

    monkeypatch.setattr(src, "get", fake_get)
    company = SimpleNamespace(slug="anthropic", ids=lambda page: {"hn_queries": ["Anthropic", "Claude"]})
    assert [r["dims"]["id"] for r in src.hn_stories(company)] == ["1", "3"] and seen == ["Anthropic", "Claude"]


def test_the_post_and_repository_parsers_value_items_at_reactions_and_stars():
    [post] = list(
        src.parse_posts(
            [
                {
                    "id": 5,
                    "url": "https://dev.to/p",
                    "title": "T",
                    "published_at": "2026-09-28T10:00:00Z",
                    "public_reactions_count": 110,
                    "comments_count": 4,
                    "user": {"username": "m"},
                },
                {"id": 6},
            ],
            "claude",
            "anthropic",
        )
    )
    assert post["value"] == 110 and post["dims"]["author"] == "m" and post["source_url"] == "https://dev.to/p"
    [repo] = list(
        src.parse_repos(
            {
                "items": [
                    {
                        "full_name": "o/r",
                        "html_url": "https://github.com/o/r",
                        "created_at": "2026-09-23T10:00:00Z",
                        "stargazers_count": 5293,
                        "description": "d",
                        "language": "Go",
                    }
                ]
            },
            "anthropic",
        )
    )
    assert repo["value"] == 5293 and repo["dims"]["language"] == "Go" and repo["as_of"] == "2026-09-23"
    for row, sid in ((post, "community_posts"), (repo, "github_repos")):
        validate(
            "observation",
            {**row, "source": sid, "method": "api", "tier": "platform", "retrieved_at": "2026-10-06T08:00:00Z"},
        )


def test_only_the_target_company_is_collected(monkeypatch):
    monkeypatch.setattr(src, "get", lambda *a, **k: pytest.fail("a peer must not be fetched"))
    monkeypatch.setattr(src, "github_get", lambda *a, **k: pytest.fail("a peer must not be fetched"))
    peer = load_companies(ROOT)["openai"]
    for collector in (src.news_headlines, src.hn_stories, src.ai_feeds, src.community_posts, src.github_repos):
        assert list(collector(peer)) == []


# ---- picking within each kind ----


def story(title, outlet, published="2026-10-05T10:00:00Z"):
    return {"title": title, "outlet": outlet, "published": published, "source_url": f"https://n.example/{outlet}"}


def test_headlines_about_one_story_are_grouped_even_when_the_words_differ():
    assert m.tokens("Executives testifies at hearing") == m.tokens("Executives testify at hearing")
    items = [
        story("OpenAI, Google testify at NYC hearing", "Reuters"),
        story("NYC council hearing: Google, OpenAI testify", "Bloomberg"),
        story("Anthropic opens an office in Seoul", "Yonhap"),
    ]
    assert sorted(len(g) for g in m.clusters(items, 0.3, 2)) == [1, 2]


def test_a_story_needs_two_outlets_the_most_covered_come_first_and_a_repeat_is_skipped():
    df = pd.DataFrame(
        [
            story("Chip deal for Broadcom financed by banks", "A"),
            story("Banks finance chip deal for Broadcom", "B"),
            story("Banks finance Broadcom chip deal worth billions", "C"),
            story("Seoul office opens for the lab", "D"),
            story("Seoul office for the lab opens", "E"),
            story("A lone headline about quarterly pricing", "F"),
        ]
    )
    picks = m.news_picks(df, CFG["news"], 3, 7)
    assert [p["why"].split(" outlets")[0] for p in picks] == ["3", "2"]  # the lone headline never qualifies
    assert len(m.news_picks(df, CFG["news"], 1, 7)) == 1  # places limit


def test_discussion_community_and_trend_rank_by_their_own_number_above_their_floor():
    hn = pd.DataFrame(
        [
            {
                "title": "Big",
                "value": 721,
                "comments": 558,
                "created": "2026-10-05T09:00:00Z",
                "source_url": "https://hn/1",
            },
            {
                "title": "Small",
                "value": 20,
                "comments": 2,
                "created": "2026-10-05T09:00:00Z",
                "source_url": "https://hn/2",
            },
        ]
    )
    [pick] = m.discussion_picks(hn, CFG["discussion"], 2)
    assert pick["headline"] == "Big" and pick["why"].startswith("721 points, 558 comments")
    posts = pd.DataFrame(
        [
            {
                "title": "P",
                "value": 110,
                "comments": 4,
                "author": "m",
                "published": "2026-09-28T10:00:00Z",
                "source_url": "https://dev/p",
            },
            {
                "title": "Q",
                "value": 3,
                "comments": 0,
                "author": "n",
                "published": "2026-09-28T10:00:00Z",
                "source_url": "https://dev/q",
            },
        ]
    )
    [post] = m.community_picks(posts, CFG["community"], 2)
    assert post["why"] == "110 reactions, 4 comments on dev.to, by m"
    repos = pd.DataFrame(
        [
            {
                "name": "o/big",
                "value": 5293,
                "created": "2026-09-23T10:00:00Z",
                "language": "Go",
                "description": "x " * 100,
                "source_url": "https://gh/o/big",
            },
            {
                "name": "o/tiny",
                "value": 12,
                "created": "2026-09-23T10:00:00Z",
                "language": None,
                "description": None,
                "source_url": "https://gh/o/tiny",
            },
        ]
    )
    [repo] = m.trend_picks(repos, CFG["trend"], 2)
    assert (
        repo["headline"] == "o/big"
        and repo["why"].startswith("5,293 stars since it was created on 2026-09-23; Go.")
        and repo["why"].endswith("…")
    )


def test_blogs_need_the_name_in_the_title_and_the_newest_come_first():
    df = pd.DataFrame(
        [
            {
                "title": "Old post on Claude",
                "outlet": "A",
                "published": "2026-09-20T10:00:00Z",
                "source_url": "https://a/1",
                "in_title": True,
            },
            {
                "title": "New post on Anthropic",
                "outlet": "B",
                "published": "2026-10-04T10:00:00Z",
                "source_url": "https://b/1",
                "in_title": True,
            },
            {
                "title": "Mentions it only in the body",
                "outlet": "C",
                "published": "2026-10-05T10:00:00Z",
                "source_url": "https://c/1",
                "in_title": False,
            },
        ]
    )
    picks = m.blog_picks(df, 2)
    assert [p["headline"] for p in picks] == [
        "New post on Anthropic",
        "Old post on Claude",
    ] and "names Anthropic or Claude in the title" in picks[0]["why"]


def test_filings_need_enough_distinct_filers_and_say_that_a_mention_is_not_a_purchase():
    df = pd.DataFrame(
        [
            {"cik": "1", "filer": "ACME CORP", "form": "10-Q", "as_of": "2026-10-03", "source_url": "https://sec/1"},
            {"cik": "2", "filer": "BETA INC", "form": "10-K", "as_of": "2026-10-04", "source_url": "https://sec/2"},
        ]
    )
    [pick] = m.filing_picks(df, {"min_filers": 2}, 1)
    assert (
        pick["why"].startswith("2 filers")
        and "Beta Inc (10-K)" in pick["why"]
        and "not a purchase" in pick["why"]
        and pick["link"] == "https://sec/2"
    )
    assert m.filing_picks(df.iloc[:1], {"min_filers": 2}, 1) == []


def read(sid, chg, prev=None):
    return {"id": sid, "label": sid, "chg": chg, "prev": prev, "text": "100", "based": "Jul to Sep 2026", "freq": "M"}


def test_insights_put_fired_tripwires_first_then_the_widest_moves_and_skip_flat_and_excluded_ones():
    cfg_b = {"f": {"page": "Page", "signals": {s: {"band": 0.10} for s in ("a", "b", "c", "d", "marks")}}}
    reads = {"f": [read("a", 0.25), read("b", 0.60, 0.1), read("c", 0.05), read("d", -0.50), read("marks", 1.27)]}
    rules = [{"id": "t", "label": "A fell", "signal": "d", "rule": {"type": "change_below", "value": -0.4}}]
    picks = m.insight_picks(cfg_b, reads, rules, {"min_bands": 2, "exclude": ["marks"]}, 3, date(2026, 10, 6))
    assert [p["headline"].split(":")[0] for p in picks] == ["A fell", "b", "a"]
    assert (
        picks[0]["why"].startswith("Tripwire fired")
        and picks[1]["why"].startswith("6.0× its flat band")
        and all(p["link"] is None for p in picks)
    )


# ---- the charts: week and month ----


def obs(source, metric, as_of, value=1, retrieved="2026-10-06T08:00:00Z", **dims):
    return {
        "source": source,
        "source_url": f"https://x/{source}/{dims.get('title') or dims.get('name') or as_of}",
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


def feed(title, outlet, day, kind="news", in_title=True):
    return obs(
        "ai_feeds",
        "feed_item",
        day,
        title=title,
        outlet=outlet,
        kind=kind,
        published=f"{day}T10:00:00Z",
        in_title=in_title,
    )


def world():
    return Ctx(
        [
            obs(
                "news_headlines",
                "news_headline",
                "2026-10-05",
                title="Broadcom chip deal financed by banks",
                outlet="Reuters",
                published="2026-10-05T10:00:00Z",
            ),
            feed("Banks finance Broadcom chip deal", "Axios", "2026-10-05"),
            obs(
                "news_headlines",
                "news_headline",
                "2026-09-20",
                title="Seoul office opens for the lab",
                outlet="Reuters",
                published="2026-09-20T10:00:00Z",
            ),
            feed("Seoul office for the lab opens", "Axios", "2026-09-20"),
            feed("A post on Claude Code", "Simon Willison", "2026-09-25", kind="blog"),
            obs(
                "hn_stories",
                "hn_story_points",
                "2026-10-04",
                300,
                id="1",
                title="Fresh story",
                comments=10,
                url=None,
                created="2026-10-04T10:00:00Z",
            ),
            obs(
                "hn_stories",
                "hn_story_points",
                "2026-09-15",
                900,
                id="2",
                title="Older bigger story",
                comments=50,
                url=None,
                created="2026-09-15T10:00:00Z",
            ),
            obs(
                "community_posts",
                "post_reactions",
                "2026-09-28",
                40,
                id="p",
                title="A dev.to post",
                comments=2,
                author="m",
                published="2026-09-28T10:00:00Z",
                tag="claude",
            ),
            obs(
                "github_repos",
                "repo_stars",
                "2026-10-02",
                500,
                name="o/new",
                description="d",
                language="Go",
                created="2026-10-02T10:00:00Z",
            ),
            obs(
                "github_repos",
                "repo_stars",
                "2026-09-12",
                800,
                name="o/older",
                description="d",
                language="Rust",
                created="2026-09-12T10:00:00Z",
            ),
        ],
        NAMES,
    )


def test_the_week_holds_only_the_last_seven_days_and_the_month_adds_the_older_picks():
    week, month = m.this_week(world()), m.this_month(world())
    heads = lambda spec: {r["headline"] for r in spec["rows"]}
    assert heads(week) == {"Broadcom chip deal financed by banks", "Fresh story", "o/new"}
    assert {
        "Seoul office opens for the lab",
        "A post on Claude Code",
        "Older bigger story",
        "A dev.to post",
        "o/older",
    } <= heads(month) and heads(week) <= heads(month)
    assert week["subtitle"].endswith("in the 7 days to 2026-10-06") and month["subtitle"].endswith(
        "in the 30 days to 2026-10-06"
    )
    assert week["title"] == "This week's Hot Pick" and month["title"] == "This month's Hot Pick"
    assert [r["rank"] for r in month["rows"]] == list(range(1, len(month["rows"]) + 1))
    assert month["takeaway"][0].startswith(f"{len(month['rows'])} picks in the 30 days to 2026-10-06")


def test_news_outlets_from_google_news_and_publisher_feeds_are_counted_together():
    [news] = [r for r in m.this_week(world())["rows"] if r["kind"] == "News"]
    assert news["why"].startswith("2 outlets in 7 days") and "Reuters" in news["why"] and "Axios" in news["why"]


def test_a_quiet_period_shows_fewer_picks_not_filler_and_old_items_are_not_picked():
    quiet = Ctx(
        [
            feed("Old chip deal financed by banks", "A", "2026-09-01"),
            feed("Banks finance old chip deal", "B", "2026-09-01"),
            obs(
                "hn_stories",
                "hn_story_points",
                "2026-10-05",
                5,
                id="9",
                title="x",
                comments=0,
                url=None,
                created="2026-10-05T00:00:00Z",
            ),
        ],
        NAMES,
    )
    assert m.this_week(quiet)["rows"] == [] and m.this_month(quiet)["rows"] == []


def test_the_window_ends_on_the_newest_collection_of_the_news_and_discussion_sources():
    assert m.window_end(world()) == date(2026, 10, 6) and m.window_end(Ctx([], NAMES)) is None


def test_both_charts_are_awaiting_data_when_nothing_has_been_collected():
    registry.discover()
    for mid in ("hot_pick.this_week", "hot_pick.this_month"):
        spec = build_mart(registry.MARTS[mid], {sid: [] for sid in m.SOURCES}, datetime(2026, 10, 6, tzinfo=UTC), NAMES)
        assert spec["status"] == "awaiting_data" and spec["rows"] == []


def test_the_config_is_consistent():
    assert set(CFG["order"]) == set(m.LABEL)
    for window, spec in CFG["windows"].items():
        assert set(spec["places"]) == set(CFG["order"]), window
        assert sum(spec["places"].values()) <= 13 and spec["days"] in (7, 30)
    assert set(CFG["insights"]["exclude"]) <= {s for f in briefing.config().values() for s in f["signals"]}


# ---- saving a week and a month ----


@pytest.fixture
def root(tmp_path):
    for sub in ("config/companies", "config/identifiers", "config/metrics"):
        (tmp_path / sub).mkdir(parents=True)
    for f in (ROOT / "config/companies").glob("*.yaml"):
        (tmp_path / "config/companies" / f.name).write_text(f.read_text())
    for name in ("briefing.yaml", "tripwires.yaml", "hot_pick.yaml"):
        (tmp_path / "config" / name).write_text((ROOT / "config" / name).read_text())
    return tmp_path


def test_freezing_saves_a_valid_week_and_month_and_never_overwrites_a_past_one(root, monkeypatch):
    registry.discover()
    rows = {r["source"]: [] for r in world().df.to_dict("records")}
    for r in world().df.to_dict("records"):
        rows[r["source"]].append(r)
    monkeypatch.setattr(freezer, "read_observations", lambda r, meta: rows.get(meta["id"], []))
    monkeypatch.setattr(m, "ROOT", root)
    monkeypatch.setattr(briefing, "ROOT", root)
    week, month = freezer.freeze(root, now=datetime(2026, 10, 7, 9, tzinfo=UTC))
    assert (week.name, month.name) == ("hot_pick.week_2026_41.json", "hot_pick.month_2026_10.json")
    for path, mart_id, title in (
        (week, "hot_pick.week_2026_41", "Hot Pick, week 2026-W41"),
        (month, "hot_pick.month_2026_10", "Hot Pick, October 2026"),
    ):
        spec = json.loads(path.read_text())
        assert spec["id"] == mart_id and spec["title"] == title
        validate("chart_spec", spec)
    assert freezer.freeze(root, now=datetime(2026, 10, 7, 9, tzinfo=UTC)) == [
        week,
        month,
    ]  # the running week and month can be saved again
    with pytest.raises(SystemExit, match="never overwritten"):
        freezer.freeze(root, now=datetime(2026, 10, 14, tzinfo=UTC))  # a week later the week file is a past week
