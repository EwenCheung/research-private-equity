import json
from datetime import date

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.marts import dev_adoption as marts
from pipeline.sources import dev_adoption as src

STAMP = {"method": "api", "tier": "platform", "retrieved_at": "2026-10-05T04:00:00Z"}


@pytest.fixture
def cos(monkeypatch):
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)  # every test starts from an empty store
    monkeypatch.setattr(src, "today", lambda: date(2026, 10, 5))
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    return load_companies(ROOT)


def response(body, url="https://x"):
    return httpx.Response(
        200, request=httpx.Request("GET", url), **({"text": body} if isinstance(body, str) else {"json": body})
    )


def valid(rows, source):
    for r in rows:
        validate("observation", {**r, **STAMP, "source": source})
    return rows


def test_identifiers_cover_every_company():
    ids = {c.slug: c.ids("dev_adoption") for c in load_companies(ROOT).values()}
    assert all(ids[c].get("sdk_pypi") for c in ids)  # every company has a Python SDK
    assert ids["anthropic"]["cli_npm"] == "@anthropic-ai/claude-code"


def test_pypi_rows_carry_package_and_role(cos, monkeypatch):
    sent = {}

    def fake_post(url, **kw):
        sent["q"] = kw["content"].decode()
        lines = [{"d": "2026-10-01", "project": "anthropic", "n": "5"}, {"d": "2026-10-01", "project": "mcp", "n": "9"}]
        return response("\n".join(json.dumps(x) for x in lines))

    monkeypatch.setattr(src, "post", fake_post)
    rows = valid(list(src.pypi_downloads(cos["anthropic"])), "pypi_downloads")
    assert "'anthropic','mcp'" in sent["q"] and "date >= '2023-01-01'" in sent["q"]
    assert [(r["dims"]["package"], r["dims"]["role"], r["value"]) for r in rows] == [
        ("anthropic", "sdk", 5),
        ("mcp", "mcp", 9),
    ]


def test_npm_drops_days_before_a_package_existed_and_chunks_long_ranges(cos, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        first = "/2023-01-01:" in url
        days = (
            [{"day": "2023-01-01", "downloads": 0}, {"day": "2023-01-02", "downloads": 7}]
            if first
            else [{"day": "x", "downloads": 0}]
        )
        return response({"downloads": days})

    monkeypatch.setattr(src, "get", fake_get)
    rows = valid(list(src.npm_downloads(cos["xai"])), "npm_downloads")  # xAI has no npm packages
    assert rows == [] and calls == []
    rows = list(src.npm_downloads(cos["cohere"]))
    assert [r["value"] for r in rows][:1] == [7]  # the leading zero day (before the package existed) is dropped
    assert 0 in [r["value"] for r in rows]  # a zero after the package exists is a real day and is kept
    assert len(calls) == 3  # 2023-01-01 .. 2026-10-04 in <= 501-day chunks


def test_coauthored_commits_walk_whole_weeks_with_valid_urls(cos, monkeypatch):
    seen = []

    def fake_github(url, params):
        seen.append(params["q"])
        return response({"total_count": 1000, "incomplete_results": False})

    monkeypatch.setattr(src, "github_get", fake_github)
    rows = valid(list(src.github_coauthored_commits(cos["anthropic"])), "github_coauthored_commits")
    assert rows[0]["as_of"] == "2025-02-24" and rows[-1]["as_of"] == "2026-09-28"  # Mondays, last complete week
    assert seen[0] == '"Co-Authored-By: Claude" committer-date:2025-02-24..2025-03-02'
    assert list(src.github_coauthored_commits(cos["openai"])) == []  # no trailer, no series


def test_mcp_repos_flag_the_running_month(cos, monkeypatch):
    monkeypatch.setattr(src, "github_get", lambda url, params: response({"total_count": 3}))
    rows = valid(list(src.github_mcp_server_repos(cos["anthropic"])), "github_mcp_server_repos")
    assert rows[0]["as_of"] == "2024-11-01" and rows[-1]["as_of"] == "2026-10-01"
    assert [r["dims"]["partial_month"] for r in rows[-2:]] == [False, True]
    assert list(src.github_mcp_server_repos(cos["openai"])) == []  # booked once, to Anthropic


def test_org_stats_page_through_repos(cos, monkeypatch):
    pages = {1: [{"stargazers_count": 2, "forks_count": 1}] * 100, 2: [{"stargazers_count": 5, "forks_count": 0}]}
    monkeypatch.setattr(src, "github_get", lambda url, params: response(pages[params["page"]]))
    rows = {r["metric"]: r["value"] for r in valid(list(src.github_org_stats(cos["anthropic"])), "github_org_stats")}
    assert rows == {"org_stars": 205, "org_forks": 100, "org_public_repos": 101}


# ---- marts ----

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


def obs(source, metric, entity, as_of, value, **dims):
    return {
        "source": source,
        "source_url": "https://x",
        "method": "api",
        "as_of": as_of,
        "retrieved_at": "2026-10-05T04:00:00Z",
        "tier": "platform",
        "entity": entity,
        "metric": metric,
        "value": value,
        "dims": dims,
        "entered_by": None,
        "evidence": None,
    }


def pypi_rows():
    rows = []
    for d, a, o in (("2026-08-15", 10, 30), ("2026-09-15", 20, 20), ("2026-10-03", 99, 99)):
        rows += [
            obs("pypi_downloads", "pypi_downloads", "anthropic", d, a, package="anthropic", role="sdk"),
            obs("pypi_downloads", "pypi_downloads", "openai", d, o, package="openai", role="sdk"),
        ]
    return rows


def test_share_is_arithmetic_sums_to_one_and_skips_the_running_month():
    spec = marts.python_sdk_share(Ctx(pypi_rows(), NAMES))
    months = {r["month"] for r in spec["rows"]}
    assert months == {"2026-08-01", "2026-09-01"}  # October (data to the 3rd) is still running
    for m in months:
        assert sum(r["share"] for r in spec["rows"] if r["month"] == m) == pytest.approx(1)
    assert spec["badges"] == ["arithmetic"]
    assert spec["takeaway"] == [
        "Anthropic took 50% of tracked Python SDK downloads in Sep 2026, against 25% in Aug 2026."
    ]


def test_monthly_takeaway_compares_with_the_leader():
    spec = marts.python_sdk_monthly(Ctx(pypi_rows(), NAMES))
    assert spec["rows"][0]["company"] in ("Anthropic", "OpenAI")  # display names, not slugs
    assert spec["takeaway"] == [
        "In Sep 2026, Python SDK downloads: Anthropic 20 (+100% on Aug 2026), the most of the tracked companies."
    ]


def test_snapshot_bars_report_growth_once_there_is_history():
    rows = [
        obs("vscode_installs", "vscode_installs", "anthropic", "2026-10-04", 100, extension="a"),
        obs("vscode_installs", "vscode_installs", "anthropic", "2026-10-05", 110, extension="a"),
        obs("vscode_installs", "vscode_installs", "openai", "2026-10-05", 50, extension="o"),
    ]
    spec = marts.vscode(Ctx(rows, NAMES))
    a = spec["rows"][0]
    assert (a["company"], a["value"], a["growth"], a["since"]) == ("Anthropic", 110, 0.1, "2026-10-04")
    assert spec["rows"][1]["growth"] is None  # one snapshot: no growth yet
    assert spec["takeaway"] == ["Anthropic leads with 110 installs, 2.2× OpenAI."]


def test_spike_days_are_excluded_from_the_chart_but_kept_in_the_table():
    rows = [
        obs("npm_downloads", "npm_downloads", "openai", f"2026-08-{d:02d}", 100, package="@openai/codex", role="cli")
        for d in range(1, 32)
    ]
    rows += [
        obs(
            "npm_downloads",
            "npm_downloads",
            "openai",
            f"2026-09-{d:02d}",
            5000 if d in (2, 3) else 100,
            package="@openai/codex",
            role="cli",
        )
        for d in range(1, 31)
    ]
    spec = marts.coding_agent_cli(Ctx(rows, NAMES))
    sep = next(r for r in spec["rows"] if r["month"] == "2026-09-01")
    assert (sep["downloads"], sep["raw"], sep["spike_days"]) == (2800, 12800, 2)
    assert any("OpenAI Sep 2026 (2 days, raw 12.8K)" in a for a in spec["assumptions"])


def test_a_lasting_jump_is_growth_not_a_spike():
    days = [f"2026-08-{d:02d}" for d in range(1, 32)] + [f"2026-09-{d:02d}" for d in range(1, 31)]
    rows = [
        obs(
            "npm_downloads", "npm_downloads", "anthropic", d, 100 if d < "2026-08-20" else 5000, package="p", role="cli"
        )
        for d in days
    ]
    spec = marts.coding_agent_cli(Ctx(rows, NAMES))
    assert all(r["spike_days"] == 0 for r in spec["rows"])  # a launch-style step up stays in the series
