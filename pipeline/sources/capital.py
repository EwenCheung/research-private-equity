"""Capital and valuation: four cited ledgers (funding rounds, run-rate statements, Amazon's filings), SEC Form D filings that
name Anthropic (all third-party vehicles) and mutual-fund N-PORT holdings of Anthropic shares.

`uv run python -m pipeline.sources.capital` re-fetches every page the ledgers cite and fails if a quote is not word for word there.
"""

import csv
import html
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from pipeline.core import ROOT, source
from pipeline.core.http import get, sec_get
from pipeline.core.store import latest_as_of

PAGE = "capital"
SEC_SEARCH = "https://efts.sec.gov/LATEST/search-index"
EDGAR = "https://www.sec.gov/Archives/edgar/data"
NPORT_NS = {"n": "http://www.sec.gov/edgar/nport"}
OVERLAP_DAYS = 14  # filings can appear in the index a few days after they are made


def today() -> date:
    return datetime.now(UTC).date()


# ---- cited ledgers: rows live in data/ledgers/<id>.csv, each with a link, a verbatim quote and who entered it ----


def ledger(**meta):
    """A ledger source collects nothing: the core reads its CSV. Declared so it has an SLA, a label and caveats."""

    def register(fn):
        return source(
            **{"page": PAGE, "method": "ledger", "backfillable": True, "cadence": "monthly", "sla_days": 35, **meta}
        )(fn)

    return register


@ledger(
    id="capital_funding_rounds",
    label="Anthropic funding rounds, from Anthropic's newsroom (cited ledger)",
    url="https://www.anthropic.com/news",
    tier="company-stated",
    caveats="Every round Anthropic announced itself, with the amount, lead investors and (where it stated one) the post-money valuation quoted "
    "from the announcement. Series A to C announcements state no valuation, and Series D was never announced by Anthropic, so those are "
    "gaps, not zeros. Hand-maintained: add a row when a round is announced.",
)
def capital_funding_rounds(company):
    return iter(())


@ledger(
    id="capital_arr_milestones",
    label="Anthropic run-rate revenue statements (cited ledger)",
    url="https://www.anthropic.com/news",
    tier="company-stated",
    caveats="Run-rate figures Anthropic gave in its own announcements, with the sentence quoted. A run-rate is the company's own annualised "
    "projection from a recent period, not recognised revenue. The date is the date the sentence describes (stated in dims as_of_basis).",
)
def capital_arr_milestones(company):
    return iter(())


@ledger(
    id="capital_arr_press",
    label="Press-reported Anthropic revenue figures (cited ledger)",
    url="https://www.cnbc.com/",
    tier="press",
    caveats="Figures reported by news outlets citing sources or documents, not by Anthropic. Kept apart from the company-stated ledger. "
    "Reports of leaked prospectus numbers are unverified by us; an S-1 has not been made public on EDGAR yet.",
)
def capital_arr_press(company):
    return iter(())


@ledger(
    id="capital_strategic_holders",
    label="Amazon 10-K and 10-Q passages on its Anthropic investment (cited ledger)",
    url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001018724&type=10-Q",
    tier="filing",
    cadence="quarterly",
    sla_days=100,
    caveats="Amounts Amazon reports for its convertible notes and nonvoting preferred stock in Anthropic, quoted from its filings. "
    "Alphabet's filings never name Anthropic, so there is no Alphabet row. Amazon does not disclose a share count or ownership percentage.",
)
def capital_strategic_holders(company):
    return iter(())


# ---- SEC full-text search: Form D (third-party vehicles) and N-PORT (mutual-fund holdings) ----


def search_filings(query: str, forms: str, start: str) -> list[dict]:
    """Every EDGAR full-text hit for `query` in `forms` filed since `start`, one per accession number (the index lists a
    filing's rendered pages as separate hits), oldest first."""
    found, offset = {}, 0
    while True:
        r = sec_get(
            SEC_SEARCH,
            params={
                "q": f'"{query}"',
                "forms": forms,
                "dateRange": "custom",
                "startdt": start,
                "enddt": today().isoformat(),
                "from": offset,
            },
        ).json()
        hits = r["hits"]["hits"]
        for h in hits:
            found.setdefault(h["_source"]["adsh"], h["_source"])
        offset += len(hits)
        if not hits or offset >= r["hits"]["total"]["value"]:
            return sorted(found.values(), key=lambda s: (s["file_date"], s["adsh"]))


def filing_url(src: dict) -> str:
    cik = src["ciks"][0].lstrip("0")
    return quote(f"{EDGAR}/{cik}/{src['adsh'].replace('-', '')}/primary_doc.xml", safe=":/")


def since(company, source_id: str, floor: str) -> str:
    """Where an incremental run starts: a short overlap before the newest stored date, or `floor` the first time."""
    last = latest_as_of(company.root, source_id, company.slug)
    return (date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)).isoformat() if last else floor


def text_of(root: ElementTree.Element, path: str, ns: dict | None = None) -> str:
    return (root.findtext(path, namespaces=ns) or "").strip()


def number(text: str) -> float | None:
    """'1107500' -> 1107500.0; 'Indefinite' or '' -> None."""
    try:
        return float(text)
    except ValueError:
        return None


def normal_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", name.lower())).strip()


def issuer_class(name: str, own_names: list[str]) -> str:
    """'anthropic_own' only when the issuer is Anthropic itself; every other filer is a vehicle someone else set up to hold its shares."""
    return "anthropic_own" if normal_name(name) in own_names else "third_party_vehicle"


def sponsor_of(name: str) -> str:
    """The firm or umbrella fund behind a vehicle: 'X a Series of Y LLC' -> Y LLC, 'Y - ANTHROPIC A' -> Y, 'ANTHROPIC - Z' -> Z, else the name itself."""
    if m := re.search(r",? a series of (.+)$", name, re.IGNORECASE):
        return m.group(1).strip()
    parts = [p.strip() for p in name.split(" - ") if normal_name(p) != "anthropic"]
    return parts[0] if parts else name


def parse_form_d(xml: bytes, src: dict, own_names: list[str]) -> list[dict]:
    """Rows (without entity/source stamps) for one Form D: a count of 1, plus the amount sold when the form states a number."""
    root = ElementTree.fromstring(xml)
    name = text_of(root, "primaryIssuer/entityName")
    first = text_of(root, "offeringData/typeOfFiling/dateOfFirstSale/value")
    total = number(text_of(root, "offeringData/offeringSalesAmounts/totalOfferingAmount"))
    sold = number(text_of(root, "offeringData/offeringSalesAmounts/totalAmountSold"))
    url = filing_url(src)
    dims = {
        "adsh": src["adsh"],
        "form": src["form"],
        "filer": name,
        "filer_cik": src["ciks"][0],
        "issuer_class": issuer_class(name, own_names),
        "sponsor": sponsor_of(name),
        "entity_type": text_of(root, "primaryIssuer/entityType"),
        "fund_type": text_of(root, "offeringData/industryGroup/investmentFundInfo/investmentFundType")
        or text_of(root, "offeringData/industryGroup/industryGroupType"),
        "first_sale": first or None,
        "offering_total": total,
        "investors": number(text_of(root, "offeringData/investors/totalNumberAlreadyInvested")),
    }
    rows = [{"source_url": url, "as_of": src["file_date"], "metric": "form_d_filing", "value": 1, "dims": dims}]
    if sold is not None:
        rows.append(
            {
                "source_url": url,
                "as_of": src["file_date"],
                "metric": "form_d_amount_sold_usd",
                "value": sold,
                "dims": dims,
            }
        )
    return rows


@source(
    id="sec_form_d",
    page=PAGE,
    label="SEC EDGAR Form D filings that name Anthropic",
    url="https://efts.sec.gov/LATEST/search-index?q=%22Anthropic%22&forms=D",
    method="api",
    tier="filing",
    cadence="weekly",
    sla_days=10,
    backfillable=True,
    caveats="Every Form D the SEC full-text index finds with 'Anthropic' in it. All of them are special-purpose vehicles that other firms "
    "set up to hold Anthropic shares; none is a raise by Anthropic itself (each row carries issuer_class). Counts and amounts are the "
    "vehicles' own, so they measure secondary-market demand, not what Anthropic raised. A name containing 'Anthropic' does not prove "
    "the vehicle holds Anthropic PBC shares.",
)
def sec_form_d(company):
    ids = company.ids(PAGE)
    if not ids.get("sec_query"):
        return
    for src in search_filings(ids["sec_query"], "D", since(company, "sec_form_d", "2021-01-01")):
        for row in parse_form_d(sec_get(filing_url(src)).content, src, ids["own_issuer_names"]):
            yield row | {"entity": company.slug}


def mentions(text: str, query: str) -> bool:
    """The word itself: 'Anthropic' matches, 'Anthropics Technology Ltd.' (an unrelated company) does not."""
    return re.search(rf"\b{re.escape(query)}\b", text, re.IGNORECASE) is not None


def parse_nport(xml: bytes, src: dict, query: str) -> list[dict]:
    """Only the holdings that name Anthropic from one N-PORT-P (R7: never the whole fund), as a value row and a units row each."""
    root = ElementTree.fromstring(xml)
    gen = "n:formData/n:genInfo/n:"
    base = {
        "adsh": src["adsh"],
        "fund_family": text_of(root, gen + "regName", NPORT_NS),
        "fund": text_of(root, gen + "seriesName", NPORT_NS),
        "series_id": text_of(root, gen + "seriesId", NPORT_NS),
        "filed": src["file_date"],
    }
    as_of = text_of(root, gen + "repPdDate", NPORT_NS)
    url = filing_url(src)
    rows = []
    for line, h in enumerate(root.iterfind("n:formData/n:invstOrSecs/n:invstOrSec", NPORT_NS), 1):
        name, title = text_of(h, "n:name", NPORT_NS), text_of(h, "n:title", NPORT_NS)
        if not (mentions(name, query) or mentions(title, query)):
            continue
        dims = base | {
            "line": line,
            "name": name,
            "security": title,
            "units_type": text_of(h, "n:units", NPORT_NS),
            "issuer_category": text_of(h, "n:issuerCat", NPORT_NS) or None,
            "fair_value_level": text_of(h, "n:fairValLevel", NPORT_NS) or None,
            "pct_of_net_assets": number(text_of(h, "n:pctVal", NPORT_NS)),
        }
        for metric, field in (("nport_value_usd", "valUSD"), ("nport_units", "balance")):
            if (v := number(text_of(h, f"n:{field}", NPORT_NS))) is not None:
                rows.append({"source_url": url, "as_of": as_of, "metric": metric, "value": v, "dims": dims})
    return rows


@source(
    id="sec_nport_marks",
    page=PAGE,
    label="SEC EDGAR N-PORT-P: mutual-fund holdings of Anthropic",
    url="https://efts.sec.gov/LATEST/search-index?q=%22Anthropic%22&forms=NPORT-P",
    method="api",
    tier="filing",
    cadence="weekly",
    sla_days=10,
    backfillable=True,
    caveats="Only the Anthropic lines of each fund's quarter-end N-PORT-P (value in USD and number of shares, so the mark is value ÷ shares). "
    "Funds file each quarter's holdings about 60 days after quarter end. Marks are the funds' own fair-value estimates of restricted "
    "private shares, and different preferred series carry different prices. Anthropic's share count is not public, so no valuation is "
    "implied from them.",
)
def sec_nport_marks(company):
    ids = company.ids(PAGE)
    if not ids.get("nport_query"):
        return
    for src in search_filings(ids["nport_query"], "NPORT-P", since(company, "sec_nport_marks", "2023-01-01")):
        for row in parse_nport(sec_get(filing_url(src), timeout=180).content, src, ids["nport_query"]):
            yield row | {"entity": company.slug}


# ---- verifying the ledgers ----


def page_text(url: str) -> str:
    """The visible text of a page or SEC filing, tags replaced by spaces and whitespace collapsed."""
    r = sec_get(url, timeout=120) if "sec.gov" in url else get(url, timeout=60)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")).strip()


def quote_parts(evidence: str) -> list[str]:
    """A ledger row may quote several sentences from one page, separated by ' | '."""
    return [re.sub(r"\s+", " ", p).strip() for p in evidence.split(" | ") if p.strip()]


def verify_ledger(path: Path) -> list[str]:
    """Problems found: a page that can't be fetched, or a quote that is not word for word in it."""
    pages: dict[str, str] = {}
    problems = []
    with path.open(newline="", encoding="utf-8") as f:
        for n, rec in enumerate(csv.DictReader(f), 2):
            url = rec["source_url"]
            if url not in pages:
                try:
                    pages[url] = page_text(url)
                except Exception as e:  # noqa: BLE001 - report every bad row, not just the first
                    pages[url] = ""
                    problems.append(f"{path.name}:{n}: cannot fetch {url}: {e!r}")
            problems += [
                f"{path.name}:{n}: quote not on {url}: {p[:80]!r}"
                for p in quote_parts(rec["evidence"])
                if pages[url] and p not in pages[url]
            ]
    return problems


def main() -> int:
    problems = []
    for path in sorted((ROOT / "data" / "ledgers").glob("capital_*.csv")):
        problems += verify_ledger(path)
        print(f"checked {path.name}")
    for p in problems:
        print("ERROR", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
