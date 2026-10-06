from datetime import UTC, datetime

import httpx
import pytest

from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import capital as marts
from pipeline.sources import capital as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}


def hit(adsh, form="D", file_date="2026-03-13", cik="0002118444", file="primary_doc.xml"):
    return {"_id": f"{adsh}:{file}", "_source": {"adsh": adsh, "form": form, "file_date": file_date, "ciks": [cik]}}


# ---- Form D ----


def test_search_dedupes_a_filings_pages_and_pages_through_results(monkeypatch):
    pages = {
        0: {
            "hits": {
                "total": {"value": 3},
                "hits": [hit("A-1", file="primary_doc.xml"), hit("A-1", file="rendered.htm")],
            }
        },
        2: {"hits": {"total": {"value": 3}, "hits": [hit("B-1", file_date="2026-04-01")]}},
    }
    seen = []
    monkeypatch.setattr(
        src,
        "sec_get",
        lambda url, params=None, **k: (
            seen.append(params["from"])
            or httpx.Response(200, json=pages[params["from"]], request=httpx.Request("GET", url))
        ),
    )
    found = src.search_filings("Anthropic", "D", "2026-01-01")
    assert [f["adsh"] for f in found] == ["A-1", "B-1"] and seen == [0, 2]


def test_incremental_start_overlaps_the_newest_stored_date(monkeypatch):
    company = load_companies(ROOT)["anthropic"]
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: "2026-03-30")
    assert src.since(company, "sec_form_d", "2021-01-01") == "2026-03-16"
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    assert src.since(company, "sec_form_d", "2021-01-01") == "2021-01-01"


# ---- N-PORT ----

HOLDING = """<invstOrSec><name>{name}</name><title>{title}</title><balance>{bal}</balance><units>{units}</units><curCd>USD</curCd>
<valUSD>{val}</valUSD><pctVal>0.3</pctVal>{cat}<fairValLevel>3</fairValLevel></invstOrSec>"""
NPORT = """<?xml version="1.0" encoding="UTF-8"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData><genInfo>
<regName>Fidelity Advisor Series VII</regName><seriesName>Fidelity Advisor Technology Fund</seriesName><seriesId>S000005322</seriesId>
<repPdEnd>2026-07-31</repPdEnd><repPdDate>2025-10-31</repPdDate></genInfo><invstOrSecs>{holdings}</invstOrSecs></formData></edgarSubmission>"""


def nport(*holdings):
    body = "".join(HOLDING.format(**{"cat": "<issuerCat>CORP</issuerCat>", "units": "NS", **h}) for h in holdings)
    return NPORT.format(holdings=body).encode()


def test_nport_keeps_only_the_anthropic_lines_and_never_the_whole_fund():
    xml = nport(
        {"name": "ANTHROPIC PBC", "title": "ANTHROPIC PBC SERIES D PC PP", "bal": "195705.0", "val": "29021094.45"},
        {"name": "ALPHABET INC", "title": "ALPHABET INC CL A", "bal": "10", "val": "2000"},
        {
            "name": "Anthropics Technology Ltd.",
            "title": "Anthropics Technology Ltd., Series G",
            "bal": "5",
            "val": "50",
        },
    )
    rows = src.parse_nport(xml, hit("0000035402-25-002863", form="NPORT-P", cik="0000315700")["_source"], "Anthropic")
    assert [(r["metric"], r["value"]) for r in rows] == [("nport_value_usd", 29021094.45), ("nport_units", 195705.0)]
    d = rows[0]["dims"]
    assert rows[0]["as_of"] == "2025-10-31" and d["fund"] == "Fidelity Advisor Technology Fund" and d["line"] == 1
    assert d["security"] == "ANTHROPIC PBC SERIES D PC PP" and d["fund_family"] == "Fidelity Advisor Series VII"


def test_only_the_whole_word_anthropic_matches():
    assert src.mentions("Anthropic, Inc.", "Anthropic") and src.mentions("ANTHROPIC PBC SER B", "Anthropic")
    assert not src.mentions("Anthropics Technology Ltd.", "Anthropic")


# ---- the shipped ledgers ----


@pytest.fixture(scope="module")
def shipped():
    registry.discover()
    return {sid: read_observations(ROOT, registry.SOURCES[sid].meta) for sid in ("sec_nport_marks",)}


def test_nport_lines_on_the_real_filings_are_all_anthropic_pbc_and_never_the_other_company(shipped):
    rows = [r for r in shipped["sec_nport_marks"] if r["metric"] == "nport_units"]
    assert rows and not [r for r in rows if "anthropics" in r["dims"]["name"].lower()]
    assert all(src.mentions(r["dims"]["name"] + " " + r["dims"]["security"], "Anthropic") for r in rows)


# ---- marts ----


def obs(source, metric, as_of, value, tier="company-stated", retrieved="2026-10-05T00:00:00Z", **dims):
    return {
        "source": source, "source_url": "https://x/i", "method": "ledger", "as_of": as_of, "retrieved_at": retrieved, "tier": tier,
        "entity": "anthropic", "metric": metric, "value": value, "dims": dims, "entered_by": "t", "evidence": "q",
    }  # fmt: skip


def holding(
    adsh,
    day,
    units,
    value,
    security="ANTHROPIC PBC SERIES F PC PP",
    fund="Fund A",
    series_id="S1",
    family="Fidelity Contrafund",
    line=1,
    filed="2026-01-01",
    unit_type="NS",
    name="ANTHROPIC PBC",
    cat="CORP",
):
    d = {
        "adsh": adsh,
        "line": line,
        "fund_family": family,
        "fund": fund,
        "series_id": series_id,
        "security": security,
        "name": name,
        "units_type": unit_type,
        "issuer_category": cat,
        "filed": filed,
    }
    return [
        obs("sec_nport_marks", "nport_units", day, units, tier="filing", **d),
        obs("sec_nport_marks", "nport_value_usd", day, value, tier="filing", **d),
    ]


def test_a_mark_is_value_over_shares_lots_add_up_and_a_refiled_period_counts_once():
    rows = [
        *holding("x1", "2026-03-31", 100, 14000, line=1),
        *holding("x1", "2026-03-31", 100, 15000, line=2),  # a second lot of the same security in the same filing
        *holding(
            "x2", "2026-03-31", 200, 29000, fund="Fund A", line=1, filed="2026-08-01"
        ),  # the same period re-filed later
    ]
    df = marts.marks(Ctx(rows, NAMES))
    assert len(df) == 1 and df.iloc[0]["mark"] == pytest.approx(145.0)
    only_first = marts.marks(Ctx(rows[:4], NAMES))
    assert only_first.iloc[0]["mark"] == pytest.approx(145.0) and only_first.iloc[0]["nport_units"] == 200


def test_vehicle_units_and_economic_exposure_lines_are_not_shares_of_anthropic():
    rows = [
        *holding("x1", "2026-03-31", 100, 15000),
        *holding(
            "x2", "2026-03-31", 5000, 6e6, unit_type="OU", cat="PF", name="ANTHROPIC", fund="Coatue", series_id="S2"
        ),
        *holding(
            "x3",
            "2026-03-31",
            10,
            5000,
            name="Magnitude ANC III, LLC (economic exposure to Anthropic PBC Series B Preferred Shares)",
            fund="Other",
            series_id="S3",
        ),
    ]
    assert list(marts.marks(Ctx(rows, NAMES))["fund"]) == ["Fund A"]


def test_shares_filed_under_a_private_fund_category_still_count_when_they_are_shares():
    # BlackRock Private Investments Fund files Anthropic PBC shares (units NS) with issuer category PF: same mark as the direct holders
    rows = [*holding("x1", "2026-06-30", 4108, 2419651.03, fund="Private", series_id="S9", cat="PF")]
    assert list(marts.marks(Ctx(rows, NAMES))["fund"]) == ["Private"]


@pytest.mark.parametrize(
    ("reg", "family"),
    [("Fidelity Contrafund", "Fidelity"), ("VARIABLE INSURANCE PRODUCTS FUND III", "Fidelity"), ("T. ROWE PRICE BLUE CHIP GROWTH FUND, INC.", "T. Rowe Price"),
     ("Growth Fund of America", "Capital Group (American Funds)"), ("NYLIM VP FUNDS TRUST", "New York Life"), ("Innovation Access Fund", "Innovation Access Fund")],
)  # fmt: skip
def test_fund_family_names(reg, family):
    assert marts.family_of(reg) == family


@pytest.mark.parametrize(
    ("title", "series"),
    [("ANTHROPIC PBC SERIES D PC PP", "Series D"), ("ANTHROPIC PBC SER F-1 CVT PFD PP", "Series F-1"), ("Anthropic PBC, Series F1", "Series F-1"),
     ("ANTHROPIC PBC CL G-1 PFD PP (PHYSICAL) (NOT LISTED OR TRADING)", "Series G-1"), ("Anthropic PBC", "Unspecified"), ("ANTHROPIC PBC. SERIES H 1 TC6YZZRT6", "Series H-1")],
)  # fmt: skip
def test_series_label_from_the_security_title(title, series):
    assert marts.series_of(title) == series


def test_real_charts_validate_against_the_shipped_data(shipped):
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    for mid, m in registry.MARTS.items():
        if m.page != "capital":
            continue
        spec = build_mart(m, shipped | {s: shipped.get(s, []) for s in m.sources}, now, NAMES)
        assert spec["status"] == "ok" and spec["rows"], mid
        assert all(s["manual"] and s["manual"]["evidence"] for s in spec["sources"] if s["method"] == "ledger"), mid
