import importlib.util
from datetime import UTC, date, datetime

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT, registry
from pipeline.core.build import Ctx, build_mart
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.marts import customers as marts
from pipeline.sources import customers as src

COMPANIES = load_companies(ROOT)
NAMES = {c.slug: c.name for c in COMPANIES.values()}
STAMP = {"method": "api", "tier": "platform", "retrieved_at": "2026-10-05T08:00:00Z"}
SOURCES = ("hn_who_is_hiring", "sec_filings_naming", "usaspending_awards", "customers_kpi_claims")


def response(body):
    return httpx.Response(200, request=httpx.Request("GET", "https://x"), json=body)


def check(rows, source, tier="platform"):
    for r in rows:  # every collected row must satisfy the observation contract
        validate("observation", {**r, "source": source, **STAMP, "tier": tier})


# ---- Hacker News ----


def thread(*posts, replies=()):
    kids = [{"text": p, "children": [{"text": r} for r in replies]} for p in posts]
    return {"children": [*kids, {"text": None, "children": []}]}  # a deleted post has no text


@pytest.fixture
def hn(monkeypatch):
    src.hn_threads.cache_clear()
    src.hn_posts.cache_clear()
    monkeypatch.setattr(src.time, "sleep", lambda s: None)
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    yield monkeypatch
    src.hn_threads.cache_clear()
    src.hn_posts.cache_clear()


def serve_hn(monkeypatch, threads, trees):
    def fake_get(url, params=None, **kw):
        if url == src.HN_SEARCH:
            hits = [{"objectID": i, "title": t, "created_at": f"{d}T15:00:00Z"} for d, i, t in threads]
            return response({"hits": hits, "nbPages": 1})
        return response(trees[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(src, "get", fake_get)


def test_threads_keep_only_who_is_hiring_since_2023(hn):
    serve_hn(
        hn,
        [
            ("2026-09-01", "2", "Ask HN: Who is hiring? (September 2026)"),
            ("2026-09-01", "3", "Ask HN: Who wants to be hired? (September 2026)"),
            ("2022-12-01", "1", "Ask HN: Who is hiring? (December 2022)"),
        ],
        {},
    )
    assert src.hn_threads() == (("2026-09-01", "2"),)


def test_only_top_level_posts_count_and_a_post_naming_two_terms_counts_once(hn):
    tree = thread(
        "Acme | We build on <i>Claude</i> and Anthropic's API",
        "Beta | OpenAI, GPT-4, ChatGPT",
        "Gamma | Rust only",
        replies=["Do you use Claude too?"],  # replies are questions, not postings
    )
    serve_hn(hn, [("2026-09-01", "2", "Ask HN: Who is hiring? (September 2026)")], {"2": tree})
    rows = list(src.hn_who_is_hiring(COMPANIES["anthropic"]))
    check(rows, "hn_who_is_hiring")
    got = {(r["metric"], r["dims"].get("term")): r["value"] for r in rows}
    assert got == {
        ("hn_posts_total", None): 3,
        ("hn_posts_naming", "any"): 1,
        ("hn_posts_naming", "Claude"): 1,
        ("hn_posts_naming", "Anthropic"): 1,
    }
    assert rows[0]["source_url"] == "https://news.ycombinator.com/item?id=2"
    assert list(src.hn_who_is_hiring(COMPANIES["xai"]))[1]["value"] == 0  # xAI: no post names it


def test_collector_skips_threads_it_already_has(hn):
    serve_hn(
        hn,
        [
            ("2026-08-03", "1", "Ask HN: Who is hiring? (August 2026)"),
            ("2026-09-01", "2", "Ask HN: Who is hiring? (September 2026)"),
        ],
        {"1": thread("old"), "2": thread("new")},
    )
    hn.setattr(src, "latest_as_of", lambda *a, **k: "2026-09-01")  # 45 days overlap: August is re-read, nothing older
    assert {r["as_of"] for r in src.hn_who_is_hiring(COMPANIES["cohere"])} == {"2026-08-03", "2026-09-01"}
    hn.setattr(src, "latest_as_of", lambda *a, **k: "2026-10-01")
    assert {r["as_of"] for r in src.hn_who_is_hiring(COMPANIES["cohere"])} == {"2026-09-01"}


# Every distinct kind of input that came up in the real threads (2023-01..2026-10), and whether it is that company.
@pytest.mark.parametrize(
    ("slug", "term", "text", "hit"),
    [
        ("anthropic", "Claude", "Stack: python, claude, postgres", True),
        ("anthropic", "Claude", "uses CLAUDE.md files", True),
        ("anthropic", "Claude", "see https://claude.com/blog/x", True),
        ("anthropic", "Anthropic", "RLHF data for Anthropic's Claude", True),
        ("anthropic", "Anthropic", "philanthropic giving", False),
        ("anthropic", "Anthropic", "pip install anthropic", True),
        ("openai", "OpenAI", "imagine if Open AI was powered by Bitcoin", True),
        ("openai", "OpenAI", "an open AI ecosystem", True),  # known false hit: the pattern cannot tell, and it is rare
        ("openai", "GPT", "ChatGPT/RLHF/GPT4", True),
        ("openai", "GPT", "https://chatgptwriter.ai/jobs", False),
        ("openai", "GPT", "MemGPT agents", False),
        ("google-deepmind", "Gemini", "Vertex AI (Gemini 3 Pro)", True),
        ("google-deepmind", "Gemini", "ISAI/Capgemini", False),
        ("google-deepmind", "Gemini", "primergygemini.com", False),
        ("xai", "Grok", "ngrok is hiring", False),
        ("xai", "Grok", "grok their startup", False),
        ("xai", "Grok", "the truth-seeking AI (Grok)", True),
        ("xai", "xAI", "xAI | London, UK", True),
        ("xai", "xAI", "mspiers@x.ai", True),
        ("xai", "xAI", "Xaira Therapeutics", False),
        ("mistral", "Mistral", "LLaMA/Mistral models", True),
        ("cohere", "Cohere", "Cohere Health | Boston", False),
        ("cohere", "Cohere", "a coherent backend", False),
        ("cohere", "Cohere", "outperforms Cohere and OpenAI", True),
    ],
)
def test_hn_patterns_classify_the_real_inputs(slug, term, text, hit):
    import re

    pattern = re.compile("(?i)" + COMPANIES[slug].ids("customers")["hn"][term])
    assert bool(pattern.search(text)) is hit, (slug, term, text)


# ---- SEC EDGAR ----


def hit(
    adsh,
    file="a.htm",
    cik="0000001",
    form="10-Q",
    file_type=None,
    date_="2026-08-01",
    name="ACME INC  (ACM)  (CIK 0000001)",
    sics=("7372",),
):
    return {
        "_id": f"{adsh}:{file}",
        "_source": {"adsh": adsh, "ciks": [cik], "form": form, "file_type": file_type or form, "file_date": date_,
                    "display_names": [name], "sics": list(sics), "period_ending": "2026-06-30"},
    }  # fmt: skip


@pytest.fixture
def edgar(monkeypatch):
    monkeypatch.setattr(src, "latest_as_of", lambda *a, **k: None)
    monkeypatch.setattr(src, "today", lambda: date(2026, 10, 5))
    calls = []

    def serve(pages):  # {query: [hits...]}
        def fake(url, params=None, **kw):
            calls.append(params)
            hits = pages.get(params["q"], [])
            window = hits[params["from"] : params["from"] + 2]  # two to a page, to exercise paging
            return response({"hits": {"hits": window, "total": {"value": len(hits)}}})

        monkeypatch.setattr(src, "sec_get", fake)
        return calls

    return serve


def test_sec_main_documents_only_dedupes_filings_and_pages(edgar):
    calls = edgar(
        {
            '"Anthropic"': [
                hit("1-1"),
                hit("1-1", "ex21.htm", file_type="EX-21.1"),
                hit("2-2", cik="0000002"),
                hit("3-3", cik="0000003"),
            ],
            '"Claude Code"': [hit("1-1"), hit("4-4", cik="0000004", file="fi le.htm")],  # 1-1 matches both terms
            '"Claude Sonnet"': [hit("4-4", cik="0000004", file="fi le.htm")],  # one filing, two queries of one term
        }
    )
    rows = list(src.sec_filings_naming(COMPANIES["anthropic"]))
    check(rows, "sec_filings_naming", "filing")
    assert [(r["dims"]["term"], r["dims"]["adsh"]) for r in rows] == [
        ("Anthropic", "1-1"),
        ("Anthropic", "2-2"),
        ("Anthropic", "3-3"),
        ("Claude", "1-1"),
        ("Claude", "4-4"),
    ]
    assert rows[0]["dims"] | {"filer": "ACME INC"} == rows[0]["dims"] and rows[0]["dims"]["sic"] == "7372"
    assert rows[4]["source_url"].endswith("/data/4/44/fi%20le.htm")  # the space is encoded
    assert [c["from"] for c in calls if c["q"] == '"Anthropic"'] == [0, 2]  # a second page was requested


def test_sec_ignores_known_non_matches_and_hits_before_the_company_existed(edgar):
    edgar({'"Anthropic"': [hit("9-9", cik="0001789192", name="N2OFF, Inc.  (NITO)  (CIK 0001789192)"), hit("1-1")]})
    assert [r["dims"]["adsh"] for r in src.sec_filings_naming(COMPANIES["anthropic"])] == ["1-1"]
    edgar({'"xAI" -"Octagon"': [hit("5-5", date_="2023-05-05"), hit("6-6", date_="2024-07-24")]})
    assert [r["dims"]["adsh"] for r in src.sec_filings_naming(COMPANIES["xai"])] == [
        "6-6"
    ]  # "xai" bytes in a 2023 bank filing
    assert list(src.sec_filings_naming(COMPANIES["cohere"])) == []


def test_every_search_term_is_declared_with_why_it_is_narrow():
    for slug, c in COMPANIES.items():
        for term, spec in c.ids("customers").get("sec", {}).items():
            assert spec["queries"] and all(q.startswith('"') for q in spec["queries"]), (slug, term)
            assert all(reason for reason in spec.get("ignore", {}).values())
    bare = [q for c in COMPANIES.values() for s in c.ids("customers").get("sec", {}).values() for q in s["queries"]]
    assert (
        '"Claude"' not in bare and '"Cohere"' not in bare and '"Mistral"' not in bare
    )  # first name / Cohere Health / Mistral Equity


# ---- USAspending ----


def award(name, gid, amount=18960.0, start="2026-02-11"):
    return {"Recipient Name": name, "generated_internal_id": gid, "Award ID": gid[-5:], "Award Amount": amount, "Start Date": start,
            "End Date": "2026-03-12", "Awarding Agency": "Department of State", "Awarding Sub Agency": "Department of State",
            "Contract Award Type": "PURCHASE ORDER", "Award Type": None, "Description": "CLAUDE AI"}  # fmt: skip


def test_awards_keep_only_exact_recipient_names_and_record_a_zero(monkeypatch):
    monkeypatch.setattr(src, "today", lambda: date(2026, 10, 5))
    seen = []

    def fake_post(url, json=None, **kw):
        seen.append(json["filters"]["award_type_codes"])
        found = [
            award("ANTHROPIC, PBC", "CONT_AWD_19PCRD26K4661_1900_-NONE-_-NONE-"),
            award("AMERICAN PHILANTHROPIC LLC", "X1"),
        ]
        return response(
            {
                "results": found if json["filters"]["award_type_codes"] == ["A", "B", "C", "D"] else [],
                "page_metadata": {"hasNext": False},
            }
        )

    monkeypatch.setattr(src, "post", fake_post)
    rows = list(src.usaspending_awards(COMPANIES["anthropic"]))
    check(rows, "usaspending_awards", "filing")
    assert [r["metric"] for r in rows] == ["federal_award_obligation_usd", "federal_awards_found"]
    assert rows[0]["value"] == 18960.0 and rows[1]["value"] == 1  # PHILANTHROPIC matched the search, not the name
    assert rows[0]["source_url"] == "https://www.usaspending.gov/award/CONT_AWD_19PCRD26K4661_1900_-NONE-_-NONE-"
    assert len(seen) == len(src.AWARD_FAMILIES)  # every award family is asked: contracts, IDVs, grants, ...
    monkeypatch.setattr(
        src,
        "post",
        lambda *a, **k: response({"results": [award("MISTRAL INC", "M1")], "page_metadata": {"hasNext": False}}),
    )
    zero = list(src.usaspending_awards(COMPANIES["mistral"]))  # MISTRAL INC is a defence contractor, not Mistral AI
    assert [(r["metric"], r["value"]) for r in zero] == [("federal_awards_found", 0)]


def test_recipient_patterns_on_real_names():
    import re

    def match(slug, name):
        return any(
            re.fullmatch(p, name, re.IGNORECASE) for p in COMPANIES[slug].ids("customers")["usaspending"]["recipient"]
        )

    assert match("anthropic", "ANTHROPIC, PBC") and not match(
        "anthropic", "ASSERTIVELY PROMOTING PHILANTHROPIC SERVICES APPS"
    )
    assert (
        match("openai", "OPENAI OPCO, LLC")
        and not match("openai", "OPENAIR ACADEMY")
        and not match("openai", "OPEN AIR MRI OF AMARILLO LP")
    )
    assert match("xai", "X.AI CORP.") and not match("xai", "PRAXAIR INC") and not match("xai", "EXAI BIO INC")
    assert (
        match("cohere", "COHERE INC.") and not match("cohere", "COHERENT CORP") and not match("cohere", "ICOHERE, INC.")
    )
    assert not match("mistral", "MISTRAL INC") and match("mistral", "MISTRAL AI")


# ---- the KPI ledger ----


@pytest.fixture(scope="module")
def kpi():
    registry.discover()
    return read_observations(ROOT, registry.SOURCES["customers_kpi_claims"].meta)


def test_every_kpi_row_is_cited_to_anthropics_own_newsroom_with_a_quote(kpi):
    assert len(kpi) >= 5
    for r in kpi:  # read_observations already validated the contract; check the human fields are real
        assert r["source_url"].startswith("https://www.anthropic.com/news/")
        assert len(r["evidence"]) > 40 and r["entered_by"] == "EwenCheung via Claude"
        assert r["dims"]["what"] and r["dims"]["threshold"] and r["value"] > 0
    assert len({(r["as_of"], r["evidence"], r["value"]) for r in kpi}) == len(kpi)


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ledger", ROOT / ".claude/skills/customers-kpi-ledger/build_ledger.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ledger_build_fails_when_a_quote_is_not_on_the_page(monkeypatch, tmp_path, capsys):
    b = load_builder()
    monkeypatch.setattr(b, "OUT", tmp_path / "out.csv")
    monkeypatch.setattr(b, "page_text", lambda slug: "Today that number exceeds 400.")  # the page says something else
    assert b.main() == 1 and not (tmp_path / "out.csv").exists()
    assert "not found word for word" in capsys.readouterr().err
    monkeypatch.setattr(b, "page_text", lambda slug: " ".join([c[6] for c in b.CLAIMS] + [c[1] for c in b.CLAIMS]))
    assert b.main() == 0 and (tmp_path / "out.csv").exists()


# ---- marts ----


def obs(metric, as_of, value, entity="anthropic", retrieved="2026-10-31T00:00:00Z", source="x", **dims):
    return {"source": source, "source_url": "https://x/i", "method": "api", "as_of": as_of, "retrieved_at": retrieved,
            "tier": "platform", "entity": entity, "metric": metric, "value": value, "dims": dims,
            "entered_by": None, "evidence": None}  # fmt: skip


def hn_rows(month_day, posts, **named):
    rows = [obs("hn_posts_total", month_day, posts, thread="t")]
    for entity, (any_, term, n) in named.items():
        rows += [
            obs("hn_posts_naming", month_day, any_, entity, thread="t", term="any"),
            obs("hn_posts_naming", month_day, n, entity, thread="t", term=term),
        ]
    return rows


def test_hn_share_is_naming_over_top_level_posts_and_skips_the_running_month():
    rows = []
    for day, posts, a, o in [
        ("2025-09-01", 200, 4, 10),
        ("2026-08-03", 250, 10, 9),
        ("2026-09-01", 250, 20, 5),
        ("2026-10-01", 100, 99, 1),
    ]:
        rows += hn_rows(day, posts, anthropic=(a, "Claude", a), openai=(o, "GPT", o))
    rows = [
        {**r, "retrieved_at": "2026-10-05T00:00:00Z"} for r in rows
    ]  # collected on Oct 5: October's thread is part-filled
    ctx = Ctx(rows, NAMES)
    spec = marts.hn_share(ctx)
    assert {r["month"] for r in spec["rows"]} == {"2025-09-01", "2026-08-01", "2026-09-01"}
    assert (
        next(r for r in spec["rows"] if r["month"] == "2026-09-01" and r["company"] == "Anthropic")["share"] == 20 / 250
    )
    assert spec["takeaway"] == [
        "In Sep 2026, 8.0% of Who-is-hiring posts (20 of 250) named Claude or Anthropic, against 2.0% for OpenAI or GPT; a year earlier Anthropic was at 2.0%."
    ]
    assert set(ctx.df.loc[sorted(ctx.used), "as_of"]) == {
        "2025-09-01",
        "2026-08-03",
        "2026-09-01",
    }  # as_of is not Oct's


def test_hn_terms_share_uses_all_posts_in_the_same_months():
    rows = []
    for day in ("2025-10-01", "2026-09-01", "2023-01-02"):
        rows += hn_rows(day, 100, anthropic=(10, "Claude", 8), openai=(5, "GPT", 5))
    rows = [{**r, "retrieved_at": "2026-10-31T00:00:00Z"} for r in rows]
    spec = marts.hn_terms(Ctx(rows, NAMES))
    a = next(r for r in spec["rows"] if r["company"] == "Anthropic" and r["term"].startswith("Any"))
    assert (a["recent"], a["recent_share"], a["ever"], a["ever_share"]) == (
        20,
        20 / 200,
        30,
        30 / 300,
    )  # 2023 is outside the last 12 months


def test_sector_table_covers_every_sic_seen_and_the_no_code_bucket():
    assert marts.sector_of("7372") == "Software & data processing"
    assert marts.sector_of("2711") == "Manufacturing" and marts.sector_of("3674") == "Manufacturing"
    assert marts.sector_of("5961") == "Retail" and marts.sector_of("6770") == "Finance, insurance & real estate"
    assert marts.sector_of("4822") == "Transport, comms & utilities" and marts.sector_of("8200") == "Other services"
    assert marts.sector_of(None) == marts.sector_of("") == marts.NO_SIC
    assert marts.sector_of("0100") == "Agriculture" and marts.sector_of("1000") == "Mining"


def filing(entity, adsh, cik, as_of, sic="7372", term=None, retrieved="2026-10-31T00:00:00Z"):
    return obs(
        "sec_filing_mention",
        as_of,
        1,
        entity,
        retrieved,
        adsh=adsh,
        cik=cik,
        filer=f"F{cik}",
        form="10-Q",
        sic=sic,
        term=term or entity,
    )


def test_sec_counts_distinct_filers_unions_terms_and_drops_the_running_quarter():
    rows = [
        filing("anthropic", "a1", "1", "2026-05-10", term="Anthropic"),
        filing("anthropic", "a1", "1", "2026-05-10", term="Claude"),  # same filing found by both terms
        filing("anthropic", "a2", "1", "2026-06-01", term="Anthropic"),  # same filer, second filing
        filing("anthropic", "a3", "2", "2026-06-02", term="Anthropic"),
        filing(
            "anthropic", "a3", "2", "2026-06-02", term="Anthropic", retrieved="2026-10-01T00:00:00Z"
        ),  # re-collection
        filing("anthropic", "a4", "3", "2025-06-02", term="Anthropic"),
        filing("anthropic", "a5", "4", "2026-10-02", term="Anthropic"),  # filed in the quarter still running
        filing("openai", "o1", "1", "2026-05-11"),
        filing("openai", "o2", "2", "2026-06-03"),
        filing("openai", "o3", "5", "2026-06-04"),
    ]
    rows = [{**r, "retrieved_at": "2026-10-05T00:00:00Z"} for r in rows]
    ctx = Ctx(rows, NAMES)
    spec = marts.sec_filers(ctx)
    got = {(r["company"], r["quarter"]): r["filers"] for r in spec["rows"]}
    assert (
        got[("Anthropic", "2026-04-01")] == 2
        and got[("OpenAI", "2026-04-01")] == 3
        and got[("Anthropic", "2025-04-01")] == 1
    )
    assert ("Anthropic", "2026-07-01") in got and got[
        ("Anthropic", "2026-07-01")
    ] == 0  # an empty quarter is a zero, not a gap
    assert "2026-10-01" not in {r["quarter"] for r in spec["rows"]}
    assert spec["takeaway"] == [
        "0 distinct 10-K and 10-Q filers named Anthropic or a Claude product in Q3 2026, against 0 a year earlier and 0 naming OpenAI."
    ]
    assert "2026-10-02" not in set(ctx.df.loc[sorted(ctx.used), "as_of"])


def test_sector_chart_gives_each_filer_one_sector_and_sums_to_the_total():
    rows = [
        filing("anthropic", "a1", "1", "2026-05-10", sic=None),  # SIC blank on one filing...
        filing("anthropic", "a2", "1", "2026-08-10", sic="6282"),  # ...present on another: the filer is Finance, once
        filing("anthropic", "a3", "2", "2026-05-10", sic="7372"),
        filing("anthropic", "a4", "3", "2026-06-10", sic="7370"),
        filing("anthropic", "a5", "9", "2025-01-10", sic="7372"),  # older than four quarters
        filing("openai", "o1", "2", "2026-06-10", sic="7372"),
    ]
    rows = [{**r, "retrieved_at": "2026-10-05T00:00:00Z"} for r in rows]
    spec = marts.sec_sectors(Ctx(rows, NAMES))
    by = {r["sector"]: (r["anthropic"], r["openai"]) for r in spec["rows"]}
    assert by == {"Software & data processing": (2, 1), "Finance, insurance & real estate": (1, 0)}
    assert sum(a for a, _ in by.values()) == 3
    assert spec["takeaway"] == [
        "3 distinct filers named Anthropic or a Claude product in Q4 2025 to Q3 2026; Software & data processing is the largest sector with 2 (67%)."
    ]


def test_federal_awards_quotes_exact_dollars_and_names_who_has_none():
    rows = [
        obs(
            "federal_award_obligation_usd",
            "2026-02-11",
            18960.0,
            award_id="X",
            agency="Department of State",
            type="PURCHASE ORDER",
            description="CLAUDE AI",
            recipient="ANTHROPIC, PBC",
            end="2026-03-12",
        ),
        obs("federal_awards_found", "2026-10-05", 1),
        obs("federal_awards_found", "2026-10-05", 0, "openai"),
    ]
    spec = marts.federal_awards(Ctx(rows, NAMES))
    assert spec["takeaway"] == [
        "USAspending lists Anthropic: 1 award, $18,960 obligated under exact recipient names, and none for OpenAI."
    ]
    assert spec["rows"][0]["type"] == "Purchase Order"


def test_kpi_takeaway_computes_the_ratio_from_the_two_ledger_rows():
    def claim(day, value, what="customers spending over $1M a year (annualized)"):
        return {**obs("customer_kpi_claim", day, value, what=what, threshold="x"), "evidence": "q", "entered_by": "me"}

    spec = marts.kpi_claims(
        Ctx(
            [claim("2026-02-12", 500), claim("2026-04-06", 1000), claim("2026-04-20", 100000, "customers on Bedrock")],
            NAMES,
        )
    )
    assert spec["takeaway"] == [
        "Anthropic said customers spending over $1M a year went from over 500 on 2026-02-12 to over 1,000 on 2026-04-06, 2.0× in 53 days; each is a floor ('exceeds'), so the true ratio is not known."
    ]
    assert marts.kpi_claims(Ctx([], NAMES))["rows"] == []  # awaiting data, never an invented number


# ---- the shipped data ----


@pytest.fixture(scope="module")
def shipped():
    registry.discover()
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)
    all_rows = {sid: read_observations(ROOT, registry.SOURCES[sid].meta) for sid in SOURCES}
    return {
        mid: build_mart(m, all_rows, now, NAMES) for mid, m in registry.MARTS.items() if mid.startswith("customers.")
    }, all_rows


def test_all_six_charts_validate_against_the_shipped_data(shipped):
    specs, _ = shipped
    assert sorted(specs) == [
        f"customers.{c}" for c in ("federal_awards", "hn_share", "hn_terms", "kpi_claims", "sec_filers", "sec_sectors")
    ]
    for mid, spec in specs.items():
        assert spec["status"] == "ok" and spec["rows"], mid
    assert all(s["manual"] and s["manual"]["evidence"] for s in specs["customers.kpi_claims"]["sources"])


def test_headline_numbers_match_the_source_checked_by_hand(shipped):
    specs, _ = shipped
    sep = next(
        r for r in specs["customers.hn_share"]["rows"] if r["month"] == "2026-09-01" and r["company"] == "Anthropic"
    )
    assert (sep["naming"], sep["posts"]) == (
        17,
        253,
    )  # 253 top-level posts of the thread's 394 comments; 13 name Claude, 5 Anthropic, 1 both
    award = specs["customers.federal_awards"]["rows"]
    assert [(r["recipient"], r["obligated"], r["start"]) for r in award] == [("ANTHROPIC, PBC", 18960.0, "2026-02-11")]
    q3 = next(
        r for r in specs["customers.sec_filers"]["rows"] if r["company"] == "Anthropic" and r["quarter"] == "2026-07-01"
    )
    assert q3["filers"] == 26
    sectors = specs["customers.sec_sectors"]["rows"]
    total = int(specs["customers.sec_sectors"]["takeaway"][0].split()[0])
    assert sum(r["anthropic"] for r in sectors) == total  # each filer in exactly one sector


def test_every_shipped_filing_row_has_a_resolvable_filing_url_and_known_sector(shipped):
    _, all_rows = shipped
    for r in all_rows["sec_filings_naming"]:
        assert r["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/") and " " not in r["source_url"]
        assert marts.sector_of(r["dims"]["sic"]) != "Unmapped SIC", r["dims"]


@pytest.mark.parametrize(
    "mid", [m for m in ("hn_share", "hn_terms", "federal_awards", "kpi_claims", "sec_filers", "sec_sectors")]
)
def test_every_chart_with_no_rows_is_awaiting_data_not_an_error(mid):
    ctx = Ctx([], NAMES)
    ctx.df["retrieved_at"] = ctx.df["retrieved_at"].astype(str)
    spec = getattr(marts, mid)(ctx)
    assert spec["rows"] == [] and spec["takeaway"] == []
