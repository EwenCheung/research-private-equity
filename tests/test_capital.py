import re
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
OWN = ["anthropic", "anthropic pbc", "anthropic inc"]


def hit(adsh, form="D", file_date="2026-03-13", cik="0002118444", file="primary_doc.xml"):
    return {"_id": f"{adsh}:{file}", "_source": {"adsh": adsh, "form": form, "file_date": file_date, "ciks": [cik]}}


# ---- Form D ----

FORM_D = """<?xml version="1.0"?><edgarSubmission><submissionType>D</submissionType>
<primaryIssuer><cik>0002118444</cik><entityName>{name}</entityName><entityType>Limited Liability Company</entityType></primaryIssuer>
<offeringData><industryGroup><industryGroupType>Pooled Investment Fund</industryGroupType>
<investmentFundInfo><investmentFundType>Other Investment Fund</investmentFundType></investmentFundInfo></industryGroup>
<typeOfFiling><dateOfFirstSale>{first}</dateOfFirstSale></typeOfFiling>
<offeringSalesAmounts><totalOfferingAmount>{total}</totalOfferingAmount><totalAmountSold>{sold}</totalAmountSold></offeringSalesAmounts>
<investors><totalNumberAlreadyInvested>35</totalNumberAlreadyInvested></investors></offeringData></edgarSubmission>"""


def form_d(
    name="HII Anthropic-01, a Series of HII Anthropic, LLC",
    first="<value>2026-01-07</value>",
    total="16726593",
    sold="16726593",
):
    return FORM_D.format(name=name, first=first, total=total, sold=sold).encode()


def test_form_d_vehicle_is_third_party_and_carries_its_amount():
    src_ = hit("0002118444-26-000001")["_source"]
    rows = src.parse_form_d(form_d(), src_, OWN)
    assert [r["metric"] for r in rows] == ["form_d_filing", "form_d_amount_sold_usd"]
    assert rows[1]["value"] == 16726593.0 and rows[0]["as_of"] == "2026-03-13"
    d = rows[0]["dims"]
    assert (
        d["issuer_class"] == "third_party_vehicle"
        and d["sponsor"] == "HII Anthropic, LLC"
        and d["first_sale"] == "2026-01-07"
    )
    assert rows[0]["source_url"] == "https://www.sec.gov/Archives/edgar/data/2118444/000211844426000001/primary_doc.xml"


def test_form_d_by_anthropic_itself_is_the_only_own_filing():
    own = src.parse_form_d(form_d(name="Anthropic, PBC"), hit("0000000000-26-000001")["_source"], OWN)
    assert own[0]["dims"]["issuer_class"] == "anthropic_own"
    for name in (
        "Anthropic Capital Fund, LP",
        "Hiive Anthropic Series I a Series of Hiive Anthropic LLC",
        "Anthropic PBC 1, a Series of Venelite Venture Funds, LP",
    ):
        assert src.issuer_class(name, OWN) == "third_party_vehicle", name


def test_form_d_indefinite_offering_and_unsold_have_no_number():
    rows = src.parse_form_d(
        form_d(first="<yetToOccur/>", total="Indefinite", sold="0"),
        hit("0002118444-26-000002", form="D/A")["_source"],
        OWN,
    )
    d = rows[0]["dims"]
    assert d["first_sale"] is None and d["offering_total"] is None and d["form"] == "D/A"
    assert rows[1]["value"] == 0.0


@pytest.mark.parametrize(
    ("name", "sponsor"),
    [
        ("Anthropic Jan 2026 a Series of CGF2021 LLC", "CGF2021 LLC"),
        ("AUGUREY VENTURES I, LLC - ANTHROPIC A", "AUGUREY VENTURES I, LLC"),
        ("ANTHROPIC - MYASIAVC ALTERNATE FUND I", "MYASIAVC ALTERNATE FUND I"),
        ("Anthropic - Incepto AGI Funds LLC", "Incepto AGI Funds LLC"),
        ("ID Funds 3 - Anthropic, a series of ID Funds 3 LLC", "ID Funds 3 LLC"),
        ("ANTHROPIC - A SERIES OF AURUM VP FUND LLC", "AURUM VP FUND LLC"),
        ("Linqto Liquidshares LLC", "Linqto Liquidshares LLC"),
    ],
)
def test_sponsor_is_the_firm_behind_the_vehicle(name, sponsor):
    assert src.sponsor_of(name) == sponsor


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


def test_peers_are_not_collected_for_capital():
    assert list(src.sec_form_d(load_companies(ROOT)["openai"])) == []
    assert list(src.sec_nport_marks(load_companies(ROOT)["openai"])) == []


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

LEDGERS = ("capital_funding_rounds", "capital_arr_milestones", "capital_arr_press", "capital_strategic_holders")
AMOUNT = re.compile(r"\$\s?([\d,.]+)\s?(billion|million|B|M)\b")


def amounts_in(text):
    return {float(n.replace(",", "")) * (1e9 if u in ("billion", "B") else 1e6) for n, u in AMOUNT.findall(text)}


@pytest.fixture(scope="module")
def shipped():
    registry.discover()
    return {
        sid: read_observations(ROOT, registry.SOURCES[sid].meta) for sid in (*LEDGERS, "sec_form_d", "sec_nport_marks")
    }


def test_every_ledger_row_is_cited_and_its_number_is_in_its_quote(shipped):
    for sid in LEDGERS:
        assert shipped[sid], sid
        for r in shipped[sid]:
            assert r["source_url"].startswith("https://") and r["entered_by"] == "EwenCheung via Claude", (
                sid,
                r["dims"],
            )
            if r["metric"] != "form_d_filing":
                assert r["value"] in amounts_in(r["evidence"]), (sid, r["metric"], r["as_of"], r["value"])


def test_ledger_tiers_are_never_mixed(shipped):
    assert {r["tier"] for r in shipped["capital_arr_milestones"]} == {"company-stated"}
    assert {r["tier"] for r in shipped["capital_arr_press"]} == {"press"}
    assert {r["tier"] for r in shipped["capital_strategic_holders"]} == {"filing"}
    assert all("anthropic.com" in r["source_url"] for r in shipped["capital_funding_rounds"])  # press has no row here


def test_post_money_only_where_the_company_states_it_and_always_with_an_amount(shipped):
    by = {}
    for r in shipped["capital_funding_rounds"]:
        by.setdefault(r["dims"]["round"], {})[r["metric"]] = r["value"]
    assert all("round_amount_usd" in v for v in by.values())
    assert by["Series H"] == {"round_amount_usd": 65e9, "round_post_money_usd": 965e9}
    assert (
        "round_post_money_usd" not in by["Series C"] and "Series D" not in by
    )  # no company-stated valuation, no announcement


def test_amazon_invested_notes_add_up_to_the_filings_own_total(shipped):
    notes = [
        r["value"]
        for r in shipped["capital_strategic_holders"]
        if r["metric"] == "holder_invested_usd" and r["dims"]["instrument"] == "convertible notes"
    ]
    assert sum(notes) == 8.0e9  # "From Q3 2023 to Q4 2025, we invested $8.0 billion in convertible notes"


def test_form_d_classification_on_the_real_filings(shipped):
    filings = [r for r in shipped["sec_form_d"] if r["metric"] == "form_d_filing"]
    assert len(filings) >= 121
    assert not [
        r for r in filings if r["dims"]["issuer_class"] == "anthropic_own"
    ]  # every filer found is a third-party vehicle
    assert all(
        r["dims"]["sponsor"].lower() != "anthropic" for r in filings
    )  # sponsor never degenerates to the target's own name
    assert all(r["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/") for r in filings)


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


def rnd(name, day, amount, post=None):
    out = [obs("capital_funding_rounds", "round_amount_usd", day, amount, round=name, leads="L", co_leads="")]
    if post:
        out.append(obs("capital_funding_rounds", "round_post_money_usd", day, post, round=name, leads="L", co_leads=""))
    return out


def arr(day, value, source="capital_arr_milestones", scope="company", **kw):
    return obs(source, "run_rate_usd", day, value, scope=scope, qualifier="exact", stated_on=day, **kw)


def test_multiple_uses_the_run_rate_nearest_in_time_and_shows_the_gap():
    rows = [
        *rnd("Series A", "2026-02-01", 1e9, 100e9),
        *rnd("Series B", "2026-06-01", 2e9, 300e9),
        arr("2026-01-01", 5e9),  # 31 days before Series A
        arr("2026-03-01", 10e9),  # 28 days after Series A, 92 before Series B
        arr("2026-06-02", 20e9),  # 1 day after Series B
        arr("2026-05-01", 99e9, scope="Claude Code"),  # a product line never counts
    ]
    spec = marts.valuation(Ctx(rows, NAMES))
    a, b = spec["rows"]
    assert (a["run_rate"], a["gap_days"], a["multiple"]) == (10e9, 28, 10.0)
    assert (b["run_rate"], b["gap_days"], b["multiple"]) == (20e9, 1, 15.0)
    assert a["formula"] == "$100B post-money ÷ $10B run-rate = 10.0x" and spec["badges"] == ["arithmetic"]


def test_a_tie_in_time_takes_the_earlier_run_rate_and_press_is_kept_apart_and_limited_to_120_days():
    rows = [
        *rnd("Series A", "2026-02-11", 1e9, 40e9),
        arr("2026-02-01", 4e9),
        arr("2026-02-21", 8e9),
        arr("2026-04-01", 5e9, source="capital_arr_press", outlet="CNBC"),  # 49 days away: used
        arr("2027-01-01", 9e9, source="capital_arr_press", outlet="CNBC"),  # far: not used
    ]
    row = marts.valuation(Ctx(rows, NAMES))["rows"][0]
    assert row["run_rate"] == 4e9 and row["multiple"] == 10.0
    assert (row["press_run_rate"], row["press_gap_days"], row["press_multiple"]) == (5e9, 49, 8.0)
    only_far = [*rnd("Series A", "2026-02-11", 1e9, 40e9), arr("2027-01-01", 9e9, source="capital_arr_press")]
    assert marts.valuation(Ctx(only_far, NAMES))["rows"][0]["press_multiple"] is None


def test_rounds_without_a_stated_valuation_have_a_blank_not_a_zero():
    spec = marts.rounds_table(
        Ctx([*rnd("Series C", "2023-05-23", 450e6), *rnd("Series E", "2025-03-03", 3.5e9, 61.5e9)], NAMES)
    )
    assert [r["post_money"] for r in spec["rows"]] == [None, 61.5e9]
    assert spec["takeaway"][0].startswith("Anthropic announced 2 funding rounds from 2023 to 2025, $4B in all")


def test_growth_columns_are_cagr_and_doubling_time_and_repeated_figures_are_skipped():
    rows = [arr("2025-01-01", 1e9), arr("2026-01-01", 2e9), arr("2026-01-15", 2e9, source="capital_arr_milestones")]
    rows[2]["source_url"] = "https://x/j"  # a second statement of the same figure two weeks later
    spec = marts.run_rate(Ctx(rows, NAMES))
    first, second, third = spec["rows"]
    assert first["growth"] is None
    assert second["growth"] == 2.0 and second["cagr"] == pytest.approx(1.0) and second["doubling_days"] == 365
    assert third["growth"] is None  # unchanged: no growth is invented
    assert "2x in 379 days" in spec["takeaway"][0]


def test_press_run_rate_is_a_separate_series_and_has_no_growth_columns():
    rows = [arr("2026-01-01", 1e9), arr("2026-02-01", 2e9, source="capital_arr_press")]
    spec = marts.run_rate(Ctx(rows, NAMES))
    assert [(r["tier"], r["growth"]) for r in spec["rows"]] == [("Company-stated", None), ("Press-reported", None)]


def fd(adsh, day, form="D", cls="third_party_vehicle", sold=1000.0):
    d = {"adsh": adsh, "form": form, "issuer_class": cls}
    return [
        obs("sec_form_d", "form_d_filing", day, 1, tier="filing", **d),
        obs("sec_form_d", "form_d_amount_sold_usd", day, sold, tier="filing", **d),
    ]


def test_form_d_counts_complete_quarters_separates_amendments_and_never_calls_a_vehicle_anthropic():
    rows = [
        *fd("a", "2025-08-01"),
        *fd("a", "2025-08-01"),  # re-collected
        *fd("b", "2026-08-05"),
        *fd("c", "2026-08-09", form="D/A"),
        *fd("d", "2026-10-02"),  # the quarter that is still running
    ]
    rows += [
        obs(
            "sec_form_d",
            "form_d_filing",
            "2026-10-03",
            1,
            retrieved="2026-10-05T00:00:00Z",
            tier="filing",
            adsh="z",
            form="D",
            issuer_class="third_party_vehicle",
        )
    ]
    spec = marts.form_d(Ctx(rows, NAMES))
    got = {(r["quarter"], r["kind"]): (r["filings"], r["amount_sold"]) for r in spec["rows"]}
    assert got == {
        ("2025 Q3", marts.NEW_VEHICLE): (1, 1000.0),
        ("2026 Q3", marts.NEW_VEHICLE): (1, 1000.0),
        ("2026 Q3", marts.AMENDMENT): (1, None),
    }
    assert (
        spec["takeaway"][0]
        == "5 Form D filings name Anthropic since 2025: 0 filed by Anthropic itself and 5 by vehicles other firms set up to hold its shares."
    )
    assert spec["takeaway"][1] == "1 new vehicles filed in Q3 2026, against 1 in Q3 2025."


def test_form_d_missing_amounts_stay_blank_instead_of_becoming_zero():
    rows = [
        obs(
            "sec_form_d",
            "form_d_filing",
            "2026-03-13",
            1,
            tier="filing",
            adsh="a1",
            form="D",
            issuer_class="third_party_vehicle",
        )
    ]
    spec = marts.form_d(Ctx(rows, NAMES))
    assert spec["rows"] == [{"quarter": "2026 Q1", "kind": marts.NEW_VEHICLE, "filings": 1, "amount_sold": None}]


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


def test_change_since_the_round_uses_the_mark_nearest_it_and_shows_no_change_when_it_is_the_latest():
    rows = [
        *rnd("Series H", "2026-05-28", 65e9, 965e9),
        # Fund A: marks Apr 30 (28 days before the round), Jul 31
        *holding("a1", "2026-04-30", 100, 25914, fund="Fund A", series_id="A"),
        *holding("a2", "2026-07-31", 100, 58900, fund="Fund A", series_id="A"),
        # Fund B: Mar 31 and Jun 30 (33 days after): nearest is the latest, so no change
        *holding("b1", "2026-03-31", 100, 25914, fund="Fund B", series_id="B"),
        *holding("b2", "2026-06-30", 100, 58900, fund="Fund B", series_id="B"),
        # Fund C: a single mark: left out
        *holding("c1", "2026-06-30", 100, 58900, fund="Fund C", series_id="C"),
    ]
    spec = marts.fund_mark_changes(Ctx(rows, NAMES))
    a, b = spec["rows"]
    assert (a["fund"], a["round_mark_gap_days"], a["round_mark"], a["latest_mark"]) == ("Fund A", -28, 259.14, 589.0)
    assert a["change"] == pytest.approx(589.0 / 259.14 - 1) and b["fund"] == "Fund B" and b["change"] is None
    assert (
        "1 of 1 fund lines" not in spec["takeaway"][0]
        and "1 higher; changes run from +127% to +127%." in spec["takeaway"][0]
    )
    assert any("1 lines with a single mark are left out" in a_ for a_ in spec["assumptions"])


def test_amazon_cash_counts_both_five_billion_investments_made_in_the_same_quarter():
    def hold(day, metric, value, instrument, **kw):
        return obs(
            "capital_strategic_holders", metric, day, value, tier="filing", holder="Amazon", instrument=instrument, **kw
        )

    rows = [
        hold("2025-12-31", "holder_value_usd", 40e9, "convertible notes", basis="b"),
        hold("2025-12-31", "holder_value_usd", 10e9, "nonvoting preferred stock", basis="b"),
        hold("2026-06-30", "holder_value_usd", 90e9, "convertible notes", basis="b"),
        hold("2026-06-30", "holder_value_usd", 60e9, "nonvoting preferred stock", basis="b"),
        {
            **hold("2026-06-30", "holder_invested_usd", 5e9, "nonvoting preferred stock", round="Series G"),
            "evidence": "G",
        },
        {
            **hold("2026-06-30", "holder_invested_usd", 5e9, "nonvoting preferred stock", round="Series H"),
            "evidence": "H",
        },
    ]
    spec = marts.amazon(Ctx(rows, NAMES))
    assert spec["takeaway"] == [
        "Amazon records $150B for its Anthropic convertible notes and nonvoting preferred stock at 2026-06-30, against $50B at 2025-12-31.",
        "It reports investing $10B in cash since 2023, so the carrying value is 15.0x the cash invested.",
    ]


def test_money_keeps_tenths_of_a_billion():
    assert [marts.money(x) for x in (87e6, 14e9, 61.5e9, 190.4e9, 965e9)] == [
        "$87M",
        "$14B",
        "$61.5B",
        "$190.4B",
        "$965B",
    ]


def test_real_charts_validate_against_the_shipped_data(shipped):
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    for mid, m in registry.MARTS.items():
        if m.page != "capital":
            continue
        spec = build_mart(m, shipped | {s: shipped.get(s, []) for s in m.sources}, now, NAMES)
        assert spec["status"] == "ok" and spec["rows"], mid
        assert all(s["manual"] and s["manual"]["evidence"] for s in spec["sources"] if s["method"] == "ledger"), mid
    # the headline numbers, recomputed by hand from the quotes
    vals = {r["round"]: r for r in build_mart(registry.MARTS["capital.valuation"], shipped, now, NAMES)["rows"]}
    assert vals["Series H"]["multiple"] == pytest.approx(965 / 47) and vals["Series F"]["multiple"] == pytest.approx(
        183 / 5
    )
    assert vals["Series E"]["gap_days"] == -61 and vals["Series G"]["gap_days"] == 0
