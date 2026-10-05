import csv
import json
from datetime import UTC, datetime

import httpx
import pytest

from contracts import validate
from pipeline.core import ROOT
from pipeline.core.build import Ctx
from pipeline.core.companies import load_companies
from pipeline.marts import hiring as marts
from pipeline.sources import hiring as src

NAMES = {c.slug: c.name for c in load_companies(ROOT).values()}
STAMP = {"method": "api", "tier": "company-stated", "retrieved_at": "2026-10-05T04:00:00Z"}


def response(body):
    return httpx.Response(
        200, request=httpx.Request("GET", "https://x"), **({"text": body} if isinstance(body, str) else {"json": body})
    )


# ---- collectors ----


def test_greenhouse_postings_are_deduped_across_departments(monkeypatch):
    src.board.cache_clear()
    body = {
        "departments": [
            {
                "name": "Sales",
                "jobs": [
                    {
                        "id": 1,
                        "title": "AE",
                        "location": {"name": "London, UK"},
                        "first_published": "2026-01-02T10:00:00-04:00",
                    }
                ],
            },
            {
                "name": "Applied AI",
                "jobs": [
                    {"id": 1, "title": "AE", "location": {"name": "London, UK"}},
                    {"id": 2, "title": "SA", "location": None},
                ],
            },
        ]
    }
    monkeypatch.setattr(src, "get", lambda url, **kw: response(body))
    url, postings = src.board("greenhouse", "anthropic")
    assert "anthropic" in url and [p["id"] for p in postings] == ["1", "2"]  # job 1 appears under two departments
    assert postings[0]["first_published"] == "2026-01-02" and postings[1]["location"] == ""
    src.board.cache_clear()


def test_ashby_unlisted_roles_are_dropped_and_countries_kept(monkeypatch):
    src.board.cache_clear()
    jobs = {
        "jobs": [
            {
                "id": "a",
                "title": "T",
                "department": "Research",
                "location": "Tokyo",
                "isListed": True,
                "address": {"postalAddress": {"addressCountry": "Japan"}},
                "secondaryLocations": [{"location": "Seoul"}],
                "publishedAt": "2026-03-12T16:38:15.322+00:00",
            },
            {"id": "b", "title": "Hidden", "isListed": False},
        ]
    }
    monkeypatch.setattr(src, "get", lambda url, **kw: response(jobs))
    _, postings = src.board("ashby", "x")
    assert [p["id"] for p in postings] == ["a"]
    assert postings[0]["location"] == "Tokyo | Seoul" and postings[0]["country"] == "Japan"
    src.board.cache_clear()


def test_live_rows_validate_and_dims_are_flat(monkeypatch):
    src.board.cache_clear()
    monkeypatch.setattr(
        src,
        "get",
        lambda url, **kw: response(
            {"departments": [{"name": "Sales", "jobs": [{"id": 9, "title": "AE", "location": {"name": "NYC"}}]}]}
        ),
    )
    co = load_companies(ROOT)["anthropic"]
    for rows, source in (
        (list(src.ats_open_roles(co)), "ats_open_roles"),
        (list(src.ats_postings(co)), "ats_postings"),
    ):
        assert rows
        for r in rows:
            validate("observation", {**r, **STAMP, "source": source})
    assert next(iter(src.ats_open_roles(co)))["value"] == 1
    assert list(src.ats_open_roles(load_companies(ROOT)["google-deepmind"])) == []  # no board configured: no rows
    src.board.cache_clear()


# ---- archived-page parsers ----

CLASSIC = """<section class="level-0"><h3>Sales</h3>
<div class="opening" department_id="1"><a href="/anthropic/jobs/1">Account Executive</a><span class="location">New York, NY</span></div>
<div class="opening" department_id="1"><a href="/anthropic/jobs/2">Partner &amp; Channel Lead</a><span class="location">London</span></div></section>"""


def test_parse_classic_greenhouse_board():
    n, posts = src.parse_capture("https://boards.greenhouse.io/anthropic", CLASSIC)
    assert n == 2 and posts[1]["title"] == "Partner & Channel Lead" and posts[0]["location"] == "New York, NY"


def test_parse_new_greenhouse_gives_the_count_but_no_titles():
    assert src.parse_capture("https://job-boards.greenhouse.io/anthropic", 'x "total":146,"total_pages":3 y') == (
        146,
        [],
    )


def test_parse_ashby_embedded_json():
    data = {
        "jobBoard": {
            "jobPostings": [
                {"title": "A", "isListed": True, "departmentName": "Eng", "locationName": "SF"},
                {"title": "B", "isListed": False},
            ]
        }
    }
    page = "<script>window.__appData = " + json.dumps(data) + ";</script>"
    n, posts = src.parse_capture("https://jobs.ashbyhq.com/openai", page)
    assert n == 1 and posts == [{"title": "A", "department": "Eng", "location": "SF"}]


def test_unparseable_capture_is_skipped_not_guessed():
    assert src.parse_capture("https://boards.greenhouse.io/x", "<html>no jobs</html>") == (None, [])
    assert src.parse_capture("https://example.com/x", "anything") == (None, [])


def test_wayback_resumes_per_board_url(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        src, "latest_as_of", lambda root, sid, slug, **dims: seen.setdefault(dims["pattern"], "2025-01-01")
    )
    monkeypatch.setattr(src, "captures", lambda pattern, since: [])
    monkeypatch.setattr(src, "get", lambda *a, **k: response(""))
    assert list(src.wayback_job_boards(load_companies(ROOT)["anthropic"])) == []
    assert set(seen) == set(
        load_companies(ROOT)["anthropic"].ids("hiring")["wayback"]
    )  # one resume point per board URL


# ---- classifiers ----


@pytest.mark.parametrize(
    ("department", "title", "expected"),
    [
        ("Sales", "Account Executive, AI Native", "Go-to-market & support"),
        ("Go To Market", "Solutions Architect", "Go-to-market & support"),
        ("Scaling", "Product Manufacturing Engineer", "Compute & data centers"),
        ("Data Center", "Electrical Engineer - Memphis", "Compute & data centers"),
        ("Compute", "Head of Compute Supply Chain", "Compute & data centers"),
        ("Applied AI", "Forward Deployed Engineer", "Applied AI & solutions"),
        ("Solutions", "Applied AI Engineer, Prototyping", "Applied AI & solutions"),
        ("Safeguards (Trust & Safety)", "Policy Analyst", "Safety, security & policy"),
        ("Security", "Security Engineer", "Safety, security & policy"),
        ("AI Research & Engineering", "Research Engineer", "Research & modeling"),
        ("Research", "Research Scientist", "Research & modeling"),
        ("Engineering & Design - Product", "Software Engineer", "Engineering & product"),
        ("Software Engineering - Infrastructure", "Staff SRE", "Engineering & product"),
        ("Finance", "Controller", "Corporate & operations"),
        ("Technical Program Management", "TPM", "Corporate & operations"),
        ("Revenue", "Customer Success Manager", "Go-to-market & support"),
        ("User Operations", "Support Delivery Lead", "Go-to-market & support"),
        ("Human Data", "AI Tutor - Croatian", "Other"),
        ("Internships", "Software Engineer Intern", "Engineering & product"),
        ("", "Research Scientist", "Research & modeling"),
        ("Confidential", "Mystery", "Other"),
        ("Data Science", "Data Scientist", "Engineering & product"),  # same bucket at every company
        ("Data Science & Analytics", "Analyst", "Engineering & product"),
        ("Product Management, Support, & Operations", "Product Manager", "Engineering & product"),
        ("Product Management & Program Management", "Program Manager", "Engineering & product"),
        ("Product Partnerships", "Partner Manager", "Go-to-market & support"),
        ("Product Policy", "Policy Lead", "Safety, security & policy"),
        ("Human Data", "Growth Recruiter", "Other"),
        ("Post-Training", "Research Engineer", "Research & modeling"),
        ("Vision", "Engineer", "Research & modeling"),
        ("Information Technology", "IT Support", "Corporate & operations"),
        ("G&A", "Office Manager", "Corporate & operations"),
        ("Business Operations", "Chief of Staff", "Corporate & operations"),
    ],
)
def test_function_rules(department, title, expected):
    assert marts.classify_function(department, title) == expected


@pytest.mark.parametrize(
    ("location", "country", "expected"),
    [
        ("San Francisco, CA | New York City, NY", "", "United States"),
        ("London, UK", "", "UK & Europe"),
        ("Tokyo, Japan", "", "Asia-Pacific"),
        ("San Francisco | London", "", "United States"),  # first listed wins
        (
            "Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA",
            "",
            "United States",
        ),  # remote token skipped
        ("Remote International", "", "Other international"),
        ("Remote-Friendly, United States", "", "United States"),
        ("Remote US", "", "United States"),
        ("Remote-Friendly (Travel-Required)", "", "Remote / unspecified"),
        ("Remote", "", "Remote / unspecified"),
        ("Zürich, CH", "", "UK & Europe"),
        ("Ontario - Remote", "", "Other international"),
        ("Montréal", "", "Other international"),
        ("Abu Dhabi", "", "Other international"),
        ("Luxembourg", "", "UK & Europe"),
        (" Southaven, MS; Memphis, TN", "", "United States"),
        ("Toronto, ON", "", "Other international"),
        ("Paris | London", "", "UK & Europe"),
        ("Seoul", "", "Asia-Pacific"),
        ("Dubai", "", "Other international"),
        ("", "", "Remote / unspecified"),
        ("Somewhere odd", "United States", "United States"),  # country field is the fallback
        ("Washington, DC", "", "United States"),
    ],
)
def test_region_rules(location, country, expected):
    assert marts.classify_region(location, country) == expected


# ---- marts ----


def obs(source, metric, entity, as_of, value, retrieved="2026-10-05T04:00:00Z", **dims):
    return {
        "source": source,
        "source_url": "https://x",
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


def posting(entity, department, title, location, as_of="2026-10-05", retrieved="2026-10-05T04:00:00Z"):
    return obs(
        "ats_postings",
        "job_posting",
        entity,
        as_of,
        1,
        retrieved,
        department=department,
        title=title,
        location=location,
        country="",
    )


def build(mart_fn, rows):
    return mart_fn(Ctx(rows, NAMES))


def test_role_mix_shares_and_rerun_dedupe():
    rows = [
        posting("anthropic", "Sales", "AE", "London, UK"),
        posting("anthropic", "Sales", "AE2", "London, UK"),
        posting("anthropic", "Finance", "Controller", "San Francisco, CA"),
        posting("anthropic", "Security", "Eng", "San Francisco, CA"),
    ]
    rows += [
        posting("anthropic", "Sales", "AE", "London, UK", retrieved="2026-10-05T03:00:00Z")
    ]  # an older re-run of the same day
    spec = build(marts.role_mix, rows)
    shares = {r["function"]: r["share"] for r in spec["rows"]}
    assert shares == {"Go-to-market & support": 0.5, "Safety, security & policy": 0.25, "Corporate & operations": 0.25}
    assert spec["takeaway"][0].startswith("Anthropic's open roles are led by go-to-market & support (50%)")


def test_region_mix_takeaway_excludes_remote_from_the_denominator():
    rows = [
        posting("anthropic", "Sales", "a", "London, UK"),
        posting("anthropic", "Sales", "b", "San Francisco, CA"),
        posting("anthropic", "Sales", "c", "Remote"),
        posting("openai", "Sales", "d", "San Francisco"),
    ]
    spec = build(marts.region_mix, rows)
    assert spec["badges"] == ["arithmetic"]
    assert spec["takeaway"] == ["50% of Anthropic's roles with a stated location are outside the US (OpenAI 0%)."]


def test_gtm_to_rd_counts_both_sides():
    rows = [
        posting("anthropic", "Sales", "a", "SF"),
        posting("anthropic", "Applied AI", "b", "SF"),
        posting("anthropic", "Research", "c", "SF"),
        posting("openai", "Research", "x", "SF"),
        posting("openai", "Go To Market", "y", "SF"),
        posting("openai", "Research", "z", "SF"),
    ]
    spec = build(marts.gtm_to_rd, rows)
    a = next(r for r in spec["rows"] if r["company"] == "Anthropic")
    assert (a["ratio"], a["customer_facing"], a["build"]) == (2.0, 2, 1)


def test_open_roles_joins_live_and_archive_history():
    rows = [
        obs("wayback_job_boards", "open_roles", "anthropic", "2024-06-05", 117, board_url="u"),
        obs("ats_open_roles", "open_roles", "anthropic", "2026-10-05", 639, board="anthropic"),
        obs("ats_open_roles", "open_roles", "openai", "2026-10-05", 827, board="openai"),
    ]
    spec = build(marts.open_roles, rows)
    assert [(r["date"], r["company"], r["roles"]) for r in spec["rows"]][:2] == [
        ("2024-06-05", "Anthropic", 117),
        ("2026-10-05", "Anthropic", 639),
    ]
    assert "117 roles in 2024-06 to 639 on 2026-10-05 (+446%)" in spec["takeaway"][0]
    head = build(marts.headline_open_roles, rows)
    a = next(r for r in head["rows"] if r["company"] == "Anthropic")
    assert (a["roles"], a["change"], a["since"]) == (639, 522, "2024-06-05")
    assert next(r for r in head["rows"] if r["company"] == "OpenAI")["change"] is None  # no snapshot 4+ weeks old yet
    assert head["takeaway"] == ["Anthropic lists 639 open roles, 188 fewer than OpenAI (827)."]


def test_h1b_charts_wait_for_data_then_roll_up_by_quarter():
    empty = build(marts.h1b_filings, [])
    assert empty["rows"] == [] and empty["takeaway"][0].startswith("Awaiting data")
    ledger = lambda metric, d, v: {
        **obs("hiring_h1b_lca", metric, "anthropic", d, v),
        "method": "ledger",
        "entered_by": "E",
        "evidence": "case X",
    }
    rows = [ledger("h1b_lca_filing", d, 1) for d in ("2026-01-10", "2026-02-10", "2026-04-10")]
    rows += [
        ledger("h1b_offered_wage_annual", d, v)
        for d, v in (("2026-01-10", 200000), ("2026-02-10", 300000), ("2026-04-10", 250000))
    ]
    f = build(marts.h1b_filings, rows)
    assert [(r["quarter"], r["filings"]) for r in f["rows"]] == [("2026-01-01", 2), ("2026-04-01", 1)]
    w = build(marts.h1b_wages, rows)
    assert [(r["quarter"], r["wage"]) for r in w["rows"]] == [("2026-01-01", 250000), ("2026-04-01", 250000)]


# ---- H-1B import ----


def write_lca(path, rows):
    cols = [
        "CASE_NUMBER",
        "CASE_STATUS",
        "DECISION_DATE",
        "VISA_CLASS",
        "EMPLOYER_NAME",
        "JOB_TITLE",
        "SOC_TITLE",
        "WAGE_RATE_OF_PAY_FROM",
        "WAGE_UNIT_OF_PAY",
        "WORKSITE_STATE",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_import_lca_keeps_tracked_employers_annualises_wages_and_never_duplicates(tmp_path):
    (tmp_path / "config/companies").mkdir(parents=True)
    (tmp_path / "config/identifiers").mkdir()
    (tmp_path / "config/companies/anthropic.yaml").write_text("slug: anthropic\nname: Anthropic\nrole: target\n")
    (tmp_path / "config/identifiers/hiring.yaml").write_text("anthropic: {lca_employers: [ANTHROPIC PBC]}\n")
    base = {
        "CASE_STATUS": "Certified",
        "DECISION_DATE": "2026-02-10",
        "VISA_CLASS": "H-1B",
        "JOB_TITLE": "Engineer",
        "SOC_TITLE": "Software",
        "WORKSITE_STATE": "CA",
    }
    f = tmp_path / "LCA_Disclosure_Data_FY2026_Q2.csv"
    write_lca(
        f,
        [
            {
                **base,
                "CASE_NUMBER": "I-1",
                "EMPLOYER_NAME": "Anthropic, PBC",
                "WAGE_RATE_OF_PAY_FROM": "100",
                "WAGE_UNIT_OF_PAY": "Hour",
            },
            {
                **base,
                "CASE_NUMBER": "I-2",
                "EMPLOYER_NAME": "ANTHROPIC PBC",
                "WAGE_RATE_OF_PAY_FROM": "250,000",
                "WAGE_UNIT_OF_PAY": "Year",
            },
            {
                **base,
                "CASE_NUMBER": "I-3",
                "EMPLOYER_NAME": "Some Other Corp",
                "WAGE_RATE_OF_PAY_FROM": "1",
                "WAGE_UNIT_OF_PAY": "Year",
            },
            {
                **base,
                "CASE_NUMBER": "I-4",
                "EMPLOYER_NAME": "Anthropic PBC",
                "VISA_CLASS": "E-3 Australian",
                "WAGE_RATE_OF_PAY_FROM": "1",
                "WAGE_UNIT_OF_PAY": "Year",
            },
        ],
    )
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    added, matched = src.import_lca(f, root=tmp_path, entered_by="EwenCheung", now=now)
    assert added == 4 and matched == {
        "Anthropic, PBC": 1,
        "ANTHROPIC PBC": 1,
    }  # 2 filings + 2 wages; other employer and E-3 skipped
    rows = list(csv.DictReader((tmp_path / src.LCA_LEDGER).open()))
    wage = {
        json.loads(r["dims"])["case_number"]: float(r["value"])
        for r in rows
        if r["metric"] == "h1b_offered_wage_annual"
    }
    assert wage == {"I-1": 208000.0, "I-2": 250000.0}  # $100/hour x 2080
    assert {r["entered_by"] for r in rows} == {"EwenCheung"} and all(
        "LCA_Disclosure_Data_FY2026_Q2.csv, case" in r["evidence"] for r in rows
    )
    assert src.import_lca(f, root=tmp_path, entered_by="EwenCheung", now=now)[0] == 0  # re-importing adds nothing
    for r in rows:  # the ledger rows are valid observations once the core stamps the source
        validate(
            "observation",
            {
                **r,
                "source": "hiring_h1b_lca",
                "method": "ledger",
                "tier": "filing",
                "value": float(r["value"]),
                "dims": json.loads(r["dims"]),
            },
        )
