"""Developer adoption: SDK and coding-agent downloads, Claude co-authored commits, GitHub orgs, VS Code, MCP ecosystem."""

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

from pipeline.core import source
from pipeline.core.http import get, github_get, post
from pipeline.core.store import latest_as_of

PAGE = "dev_adoption"
CLICKHOUSE = "https://sql-clickhouse.clickhouse.com/"
NPM_RANGE = "https://api.npmjs.org/downloads/range/{start}:{end}/{package}"
GITHUB_SEARCH = "https://api.github.com/search/{kind}"
VSCODE = "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
HISTORY_START = date(2023, 1, 1)
CLAUDE_CODE_LAUNCH = date(2025, 2, 24)  # Claude Code research preview; co-authored commits start here
MCP_LAUNCH = date(2024, 11, 1)  # MCP announced 2024-11-25; count from that month
PACKAGE = re.compile(r"^(@[a-z0-9-]+/)?[A-Za-z0-9._-]+$")


def today() -> date:
    return datetime.now(UTC).date()


def resume(company, source_id: str, start: date, overlap_days: int, **dims) -> date:
    """First date to fetch: everything on the first run, then the last stored date minus a re-check window.

    dims narrows it to one package, so a package added to config later still gets its full history.
    """
    last = latest_as_of(company.root, source_id, company.slug, **dims)
    return max(start, date.fromisoformat(last) - timedelta(days=overlap_days)) if last else start


def packages(company, *keys) -> list[tuple[str, str]]:
    """(package, role) pairs from config/identifiers/dev_adoption.yaml; role is sdk, cli or mcp."""
    ids, out = company.ids(PAGE), []
    for key in keys:
        names = ids.get(key) or []
        for name in [names] if isinstance(names, str) else names:
            if not PACKAGE.match(name):
                raise ValueError(f"suspicious package name {name!r}")
            out.append((name, key.split("_")[0]))
    return out


@source(
    id="pypi_downloads",
    page=PAGE,
    label="PyPI downloads (ClickHouse public PyPI dataset)",
    url=CLICKHOUSE,
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=3,
    backfillable=True,
    caveats="Every pip download, including CI and mirrors, so it measures relative pull, not users. "
    "ClickHouse mirrors PyPI's public BigQuery logs and lags about a day. Legacy SDKs are summed with their successors.",
)
def pypi_downloads(company):
    pkgs = dict(packages(company, "sdk_pypi", "mcp_pypi"))
    if not pkgs:
        return
    since = resume(company, "pypi_downloads", HISTORY_START, overlap_days=3)
    names = ",".join(f"'{p}'" for p in pkgs)
    query = (
        "SELECT toString(date) AS d, project, sum(count) AS n FROM pypi.pypi_downloads_per_day "
        f"WHERE project IN ({names}) AND date >= '{since}' AND date < today() "
        "GROUP BY d, project ORDER BY d FORMAT JSONEachRow"
    )
    r = post(CLICKHOUSE, params={"user": "demo"}, content=query.encode(), timeout=120)
    for line in r.text.splitlines():
        rec = json.loads(line)
        yield {
            "source_url": CLICKHOUSE,
            "as_of": rec["d"],
            "entity": company.slug,
            "metric": "pypi_downloads",
            "value": int(rec["n"]),
            "dims": {"package": rec["project"], "role": pkgs[rec["project"]]},
        }


@source(
    id="npm_downloads",
    page=PAGE,
    label="npm registry download counts",
    url="https://api.npmjs.org/downloads/range/{start}:{end}/{package}",
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=3,
    backfillable=True,
    caveats="Every npm install, including CI, so it measures relative pull, not users. "
    "Days before a package existed (zero downloads) are dropped.",
)
def npm_downloads(company):
    end = today() - timedelta(days=1)
    for package, role in packages(company, "sdk_npm", "cli_npm", "mcp_npm"):
        start = resume(company, "npm_downloads", HISTORY_START, overlap_days=3, package=package)
        started = False
        while start <= end:
            chunk_end = min(end, start + timedelta(days=500))  # the API serves at most 18 months per request
            url = NPM_RANGE.format(start=start, end=chunk_end, package=package)
            for day in get(url).json().get("downloads", []):
                started = started or day["downloads"] > 0
                if started:
                    yield {
                        "source_url": url,
                        "as_of": day["day"],
                        "entity": company.slug,
                        "metric": "npm_downloads",
                        "value": day["downloads"],
                        "dims": {"package": package, "role": role},
                    }
            start = chunk_end + timedelta(days=1)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


@source(
    id="github_coauthored_commits",
    page=PAGE,
    label="GitHub commit search: public commits co-authored by Claude",
    url="https://api.github.com/search/commits?q=%22Co-Authored-By%3A+Claude%22",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="GitHub's search count is an estimate: the same week can read 8.0M one day and 9.2M the next, "
    "and only public default branches are indexed. Read the trend, not the level; the last 3 weeks are re-checked each run.",
)
def github_coauthored_commits(company):
    trailer = company.ids(PAGE).get("coauthor_trailer")
    if not trailer:
        return
    last_full_week = week_start(today()) - timedelta(days=7)
    week = week_start(resume(company, "github_coauthored_commits", CLAUDE_CODE_LAUNCH, overlap_days=21))
    while week <= last_full_week:
        end = week + timedelta(days=6)
        q = f'"{trailer}" committer-date:{week}..{end}'
        body = github_get(GITHUB_SEARCH.format(kind="commits"), params={"q": q, "per_page": 1}).json()
        yield {
            "source_url": "https://github.com/search?" + urlencode({"type": "commits", "q": q}),
            "as_of": week.isoformat(),
            "entity": company.slug,
            "metric": "coauthored_commits",
            "value": body["total_count"],
            "dims": {"week_end": end.isoformat(), "incomplete": bool(body.get("incomplete_results"))},
        }
        week += timedelta(days=7)
        time.sleep(6)  # commit search trips GitHub's secondary rate limit well below its 30-a-minute quota


@source(
    id="github_org_stats",
    page=PAGE,
    label="GitHub organisation repositories (stars, forks, repos)",
    url="https://api.github.com/orgs/{org}/repos",
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="Totals across the org's public, non-fork repositories on the day collected. "
    "GitHub keeps no history of these totals, so the series starts when collection started.",
)
def github_org_stats(company):
    org = company.ids(PAGE).get("github_org")
    if not org:
        return
    repos, page = [], 1
    while True:
        batch = github_get(
            f"https://api.github.com/orgs/{org}/repos", params={"type": "sources", "per_page": 100, "page": page}
        ).json()
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    stamp = {"source_url": f"https://github.com/{org}", "as_of": today().isoformat(), "entity": company.slug}
    for metric, value in (
        ("org_stars", sum(r["stargazers_count"] for r in repos)),
        ("org_forks", sum(r["forks_count"] for r in repos)),
        ("org_public_repos", len(repos)),
    ):
        yield {**stamp, "metric": metric, "value": value, "dims": {"org": org}}


@source(
    id="vscode_installs",
    page=PAGE,
    label="Visual Studio Marketplace install counts",
    url=VSCODE,
    method="api",
    tier="platform",
    cadence="daily",
    sla_days=2,
    backfillable=False,
    caveats="Cumulative installs reported by the marketplace (uninstalls are not subtracted). "
    "No history is published, so growth is measured from our own snapshots.",
)
def vscode_installs(company):
    ext = company.ids(PAGE).get("vscode")
    if not ext:
        return
    body = {"filters": [{"criteria": [{"filterType": 7, "value": ext}]}], "flags": 256}
    headers = {"Accept": "application/json;api-version=7.2-preview.1"}
    found = post(VSCODE, json=body, headers=headers).json()["results"][0]["extensions"]
    for e in found:
        stats = {s["statisticName"]: s["value"] for s in e.get("statistics", [])}
        yield {
            "source_url": f"https://marketplace.visualstudio.com/items?itemName={ext}",
            "as_of": today().isoformat(),
            "entity": company.slug,
            "metric": "vscode_installs",
            "value": int(stats.get("install", 0)),
            "dims": {"extension": ext},
        }


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


@source(
    id="github_mcp_server_repos",
    page=PAGE,
    label="GitHub repository search: new repos tagged mcp-server",
    url="https://api.github.com/search/repositories?q=topic%3Amcp-server",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=8,
    backfillable=True,
    caveats="Counts public repos tagged with the mcp-server topic, by creation month. Untagged servers are missed, "
    "so it is a lower bound; the current and previous month are re-checked each run.",
)
def github_mcp_server_repos(company):
    if not company.ids(PAGE).get("mcp_npm"):  # MCP is Anthropic's protocol; the ecosystem is booked to it once
        return
    month = month_start(resume(company, "github_mcp_server_repos", MCP_LAUNCH, overlap_days=40))
    while month <= today():
        end = next_month(month) - timedelta(days=1)
        q = f"topic:mcp-server created:{month}..{end}"
        body = github_get(GITHUB_SEARCH.format(kind="repositories"), params={"q": q, "per_page": 1}).json()
        yield {
            "source_url": "https://github.com/search?" + urlencode({"type": "repositories", "q": q}),
            "as_of": month.isoformat(),
            "entity": company.slug,
            "metric": "mcp_server_repos_new",
            "value": body["total_count"],
            "dims": {"topic": "mcp-server", "partial_month": month == month_start(today())},
        }
        month = next_month(month)
        time.sleep(2.2)
