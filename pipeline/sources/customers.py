"""Customers: HN who-is-hiring posts and SEC 10-K/10-Q filers."""

import html
import re
import time
from datetime import UTC, date, datetime, timedelta
from functools import cache
from urllib.parse import quote

from pipeline.core import source
from pipeline.core.http import get, sec_get
from pipeline.core.store import latest_as_of

PAGE = "customers"
HISTORY_START = date(2023, 1, 1)
OVERLAP_DAYS = 45  # re-fetch this far back: late comments, late EDGAR indexing
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
EFTS = "https://efts.sec.gov/LATEST/search-index"
# USAspending only accepts award types from one family at a time.


def today() -> date:
    return datetime.now(UTC).date()


def since(company, source_id: str, floor: date = HISTORY_START) -> date:
    """First day to fetch: everything the first time, then the stored newest date minus a short overlap."""
    last = latest_as_of(company.root, source_id, company.slug)
    return max(floor, date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)) if last else floor


# ---- Hacker News: "Ask HN: Who is hiring?" ----


@cache
def hn_threads() -> tuple[tuple[str, str], ...]:
    """(date, id) of every monthly hiring thread since HISTORY_START, oldest first. One per month, started by whoishiring."""
    found, page = [], 0
    while True:
        r = get(HN_SEARCH, params={"tags": "story,author_whoishiring", "hitsPerPage": 100, "page": page}).json()
        found += [
            (h["created_at"][:10], h["objectID"]) for h in r["hits"] if h["title"].startswith("Ask HN: Who is hiring?")
        ]
        page += 1
        if page >= r["nbPages"]:
            return tuple(sorted(f for f in found if f[0] >= HISTORY_START.isoformat()))


@cache
def hn_posts(thread_id: str) -> tuple[str, ...]:
    """Plain text of every top-level comment (a job post) in a thread. Replies are questions, not postings. Deleted ones have no text."""
    tree = get(HN_ITEM.format(id=thread_id), timeout=120).json()
    time.sleep(0.3)
    texts = (html.unescape(re.sub(r"<[^>]+>", " ", c.get("text") or "")) for c in tree["children"])
    return tuple(t for t in texts if t.strip())


@source(
    id="hn_who_is_hiring",
    page=PAGE,
    label="Hacker News 'Who is hiring?' threads (Algolia)",
    url="https://news.ycombinator.com/submitted?id=whoishiring",
    method="api",
    tier="platform",
    cadence="weekly",
    sla_days=10,
    backfillable=True,
    caveats="Counts job posts naming a lab's model or company, per month since Jan 2023. A post that names Claude is not proof "
    "the employer pays Anthropic, and HN skews to startups and developer tools. Posts that list investors or alumni "
    '("ex-OpenAI", "backed by Anthropic\'s investors") also count. The month in progress is incomplete.',
)
def hn_who_is_hiring(company):
    terms = company.ids(PAGE).get("hn")
    if not terms:
        return
    patterns = {name: re.compile("(?i)" + p) for name, p in terms.items()}
    start = since(company, "hn_who_is_hiring").isoformat()
    for day, thread_id in hn_threads():
        if day < start:
            continue
        posts = hn_posts(thread_id)
        url = f"https://news.ycombinator.com/item?id={thread_id}"
        hits = {name: [bool(p.search(t)) for t in posts] for name, p in patterns.items()}
        rows = {"total": len(posts), "any": sum(any(h[i] for h in hits.values()) for i in range(len(posts)))}
        rows |= {name: sum(h) for name, h in hits.items()}
        yield {"source_url": url, "as_of": day, "entity": company.slug, "metric": "hn_posts_total", "value": len(posts),
               "dims": {"thread": thread_id}}  # fmt: skip
        for term in ("any", *patterns):
            yield {"source_url": url, "as_of": day, "entity": company.slug, "metric": "hn_posts_naming",
                   "value": rows[term], "dims": {"thread": thread_id, "term": term}}  # fmt: skip


# ---- SEC EDGAR full-text search: 10-K and 10-Q filers naming a company ----


def efts_hits(query: str, start: date, end: date):
    """Every hit for one full-text query over 10-K and 10-Q filings filed in [start, end], 100 to a page."""
    offset = 0
    while True:
        params = {"q": query, "forms": "10-K,10-Q", "dateRange": "custom", "startdt": start.isoformat(),
                  "enddt": end.isoformat(), "from": offset}  # fmt: skip
        data = sec_get(EFTS, params=params).json()["hits"]
        yield from (h["_source"] | {"_id": h["_id"]} for h in data["hits"])
        offset += len(data["hits"])
        if not data["hits"] or offset >= data["total"]["value"]:
            return


@source(
    id="sec_filings_naming",
    page=PAGE,
    label="SEC EDGAR full-text search (10-K and 10-Q)",
    url="https://efts.sec.gov/LATEST/search-index",
    method="api",
    tier="filing",
    cadence="weekly",
    sla_days=10,
    backfillable=True,
    caveats="Filings whose main document names the company, by filing date. The filer may be a customer, supplier, investor "
    "(Amazon, Nvidia) or competitor: this counts mentions, not customers. Exhibits are ignored. Bare 'Claude' is not "
    "searched because it is mostly a first name, and Mistral and Cohere are searched only beside another lab's name, so "
    "their counts are floors. EDGAR full-text search is the only index used; it is not audited for completeness.",
)
def sec_filings_naming(company):
    terms = company.ids(PAGE).get("sec")
    if not terms:
        return
    start, end = since(company, "sec_filings_naming"), today()
    for term, spec in terms.items():
        floor = max(start, date.fromisoformat(str(spec["since"]))) if spec.get("since") else start
        seen = set()
        for query in spec["queries"]:
            for h in efts_hits(query, floor, end):
                adsh = h["adsh"]
                cik = h["ciks"][0]
                if (
                    h["file_type"] != h["form"]
                    or adsh in seen
                    or h["file_date"] < floor.isoformat()
                    or cik in spec.get("ignore", {})
                ):
                    continue  # exhibits (certifications name people called Claude), a filing hit by two queries, known non-matches
                seen.add(adsh)
                yield {
                    "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{quote(h['_id'].split(':')[1])}",
                    "as_of": h["file_date"],
                    "entity": company.slug,
                    "metric": "sec_filing_mention",
                    "value": 1,
                    "dims": {
                        "term": term,
                        "adsh": adsh,
                        "cik": cik,
                        "filer": re.sub(r"\s+\(.*$", "", h["display_names"][0]),
                        "form": h["form"],
                        "sic": (h.get("sics") or [None])[0],
                        "period_ending": h.get("period_ending"),
                    },
                }


# ---- USAspending: federal awards to recipients named like the company ----


# ---- cited ledger: rows live in data/ledgers/<id>.csv, each with a source link, a verbatim quote and who entered it ----
