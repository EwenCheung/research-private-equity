from datetime import UTC, datetime

import httpx
import pytest

from pipeline.core import ROOT
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.marts import product as marts
from pipeline.sources import product as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


def response(body):
    return httpx.Response(
        200, request=httpx.Request("GET", "https://x"), **({"text": body} if isinstance(body, str) else {"json": body})
    )


# ---- incident parsing ----


@pytest.mark.parametrize(
    ("text", "year", "month", "start", "end"),
    [
        ("Oct 1, 16:20 - 22:36 UTC", 2026, 10, "2026-10-01T16:20", "2026-10-01T22:36"),
        (
            "Sep 30, 23:50 - Oct 1, 00:10 UTC",
            2025,
            10,
            "2025-09-30T23:50",
            "2025-10-01T00:10",
        ),  # listed under the month it ended in
        (
            "Dec 31, 23:00 - Jan 1, 01:00 UTC",
            2026,
            1,
            "2025-12-31T23:00",
            "2026-01-01T01:00",
        ),  # ...so the start is last year
        ("Nov 7, 22:47 - 22:47 UTC", 2025, 11, "2025-11-07T22:47", "2025-11-07T22:47"),
        ("Oct 1, 16:20 UTC", 2026, 10, "2026-10-01T16:20", None),  # no end time posted
    ],
)
def test_parse_span(text, year, month, start, end):
    s, e = src.parse_span(text, year, month)
    assert s.strftime("%Y-%m-%dT%H:%M") == start and (e.strftime("%Y-%m-%dT%H:%M") if e else None) == end


def test_unrecognised_time_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="unrecognised"):
        src.parse_span("Ongoing", 2026, 10)


def history_page(*months):
    return {"months": [{"name": n, "year": y, "incidents": inc} for n, y, inc in months]}


def inc(code, ts, impact="minor"):
    return {"code": code, "name": f"Incident {code}", "impact": impact, "timestamp": f"<var>{ts}</var>"}


def test_history_pages_until_the_cutoff_and_dedupes_nothing_it_does_not_need(monkeypatch):
    pages = {
        1: history_page(
            ("October", 2026, [inc("new", "Oct 1, 16:20 - 22:36 UTC", "major")]),
            ("September", 2026, []),
            ("August", 2026, []),
        ),
        2: history_page(
            ("February", 2023, [inc("old", "Feb 3, 10:00 - 11:00 UTC")]),
            ("January", 2023, [inc("older", "Jan 5, 10:00 - 11:00 UTC")]),
        ),
        3: {"months": []},
    }
    seen = []
    monkeypatch.setattr(
        src, "get", lambda url, params=None, **k: seen.append(params["page"]) or response(pages[params["page"]])
    )
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    rows = list(src.status_incidents(load_companies(ROOT)["anthropic"]))
    assert [r["dims"]["code"] for r in rows] == ["new", "old", "older"] and seen == [1, 2, 3]
    assert (
        rows[0]["dims"] | {"minutes": 376} == rows[0]["dims"]
        and rows[0]["source_url"] == "https://status.claude.com/incidents/new"
    )
    # incremental: with data to Oct 1, one page is enough and old incidents are skipped
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: "2026-10-01")
    seen.clear()
    assert [r["dims"]["code"] for r in src.status_incidents(load_companies(ROOT)["anthropic"])] == ["new"] and seen == [
        1
    ]


# ---- OpenAI status feed ----


def rss(*items):
    """Items are (title, link) or (title, link, pubDate)."""

    def item(title, link, pub=None):
        date = f"<pubDate>{pub}</pubDate>" if pub else ""
        return f"<item><title><![CDATA[{title}]]></title><link>{link}</link>{date}</item>"

    body = "".join(item(*i) for i in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>OpenAI</title>{body}</channel></rss>'


A, B, C = "01M2GA8XTS6VB3QCDEGZ0HNAQ5", "01M3Q4RK1SM4EMK445GGPG7C0N", "01KWDW344V7XWEQERJSEW62W57"


def OPENAI():
    return load_companies(ROOT)["openai"]


def test_ulid_time_matches_the_start_time_the_json_feed_gives():
    got = src.ulid_time(A)  # the JSON feed says this incident was created at 2026-09-14T15:58:48Z
    assert abs((got - datetime(2026, 9, 14, 15, 58, 48, tzinfo=UTC)).total_seconds()) < 5


def test_parse_feed_reads_the_start_from_the_id_and_skips_items_without_one():
    feed = rss(("Elevated errors", f"https://status.openai.com//incidents/{A}"), ("Odd item", "https://x/y/not-an-id"))
    rows = list(src.parse_feed(feed, OPENAI(), "status.openai.com"))
    assert len(rows) == 1
    assert rows[0]["as_of"] == "2026-09-14" and rows[0]["entity"] == "openai" and rows[0]["dims"]["impact"] == "unrated"
    assert rows[0]["source_url"] == f"https://status.openai.com/incidents/{A}" and rows[0]["dims"]["minutes"] is None


def test_a_migrated_incident_is_dated_by_the_feed_not_by_its_new_id():
    # an old incident re-imported on 2025-02-25 got a new id (minted that day) but its feed date is the real one
    migrated = "01JMXBRMFE6N2NNT7DG6XZQ6PW"
    feed = rss(
        ("Old", f"https://status.openai.com//incidents/{migrated}", "Thu, 12 Dec 2024 11:19:05 GMT"),
        ("New", f"https://status.openai.com//incidents/{A}", "Mon, 15 Sep 2026 03:00:00 GMT"),
    )
    rows = {r["dims"]["code"]: r["as_of"] for r in src.parse_feed(feed, OPENAI(), "status.openai.com")}
    assert rows == {migrated: "2024-12-12", A: "2026-09-14"}


def test_openai_live_collection_keeps_the_rated_row_and_adds_only_the_incidents_the_json_lacks(monkeypatch):
    api = {
        "incidents": [
            {"id": A, "name": "Rated", "created_at": "2026-09-14T15:58:48Z", "resolved_at": None, "impact": "major"}
        ]
    }
    feed = rss(
        ("Rated", f"https://status.openai.com//incidents/{A}"), ("Older", f"https://status.openai.com//incidents/{C}")
    )
    monkeypatch.setattr(src, "get", lambda url, **k: response(api if url.endswith("incidents.json") else feed))
    rows = list(src.status_incidents(OPENAI()))
    assert [(r["dims"]["code"], r["dims"]["impact"]) for r in rows] == [(A, "major"), (C, "unrated")]


def test_archive_reads_one_copy_a_month_and_stamps_each_row_with_the_copy_it_came_from(monkeypatch):
    copies = {
        "20250301100550": rss(("Old", f"https://status.openai.com//incidents/{C}")),
        "20250609153537": rss(("New", f"https://status.openai.com//incidents/{B}")),
    }
    asked = []

    def fake_get(url, **k):
        asked.append(url)
        if url == src.CDX:
            return response([["timestamp"], ["20250101000000"], *[[t] for t in copies]])
        return response(copies[url.split("/web/")[1].split("id_/")[0]])

    monkeypatch.setattr(src, "get", fake_get)
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    rows = list(src.status_incidents_archive(OPENAI()))
    assert [r["dims"]["code"] for r in rows] == [C, B]  # the copy from before the page existed is not read
    assert rows[0]["dims"]["capture"] == "20250301100550"
    assert rows[0]["source_url"] == "https://web.archive.org/web/20250301100550/https://status.openai.com/feed.rss"
    # a re-run starts about three months before the newest incident it already holds
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: "2026-09-14")
    assert list(src.status_incidents_archive(OPENAI())) == []
    assert not list(src.status_incidents_archive(load_companies(ROOT)["anthropic"]))


# ---- peer-comparison parsing ----


# ---- the shipped ledgers ----


# ---- marts ----


def obs(metric, as_of, value, entity="anthropic", retrieved="2026-10-31T00:00:00Z", source="x", **dims):
    return {
        "source": source,
        "source_url": "https://x/i",
        "method": "api",
        "as_of": as_of,
        "retrieved_at": retrieved,
        "tier": "company-stated",
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": None,
        "evidence": None,
    }


def incident(code, day, impact, minutes, entity="anthropic", retrieved="2026-10-31T00:00:00Z"):
    return obs(
        "incident",
        day,
        1,
        entity,
        retrieved,
        code=code,
        name=f"N {code}",
        impact=impact,
        started=f"{day}T10:00:00Z",
        resolved=None,
        minutes=minutes,
    )


def test_incidents_by_month_counts_each_incident_once_and_skips_none_and_the_running_month():
    rows = [
        incident("a", "2026-08-03", "minor", 30),
        incident("a", "2026-08-03", "minor", 30, retrieved="2026-10-30T00:00:00Z"),  # a re-collection
        incident("b", "2026-08-09", "major", 60),
        incident("c", "2026-08-20", "none", 5),
        incident("d", "2025-08-09", "minor", 10),
    ]
    ctx = Ctx(rows, NAMES)
    spec = marts.incidents_monthly(ctx)
    got = {(r["month"], r["impact"]): r["incidents"] for r in spec["rows"]}
    assert got == {("2026-08-01", "Minor"): 1, ("2026-08-01", "Major"): 1, ("2025-08-01", "Minor"): 1}
    assert spec["takeaway"] == [
        "Claude's status page logged 2 incidents in Aug 2026 (1 major or critical), against 1 in Aug 2025."
    ]
