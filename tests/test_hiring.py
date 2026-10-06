import json

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


# ---- H-1B import ----


def test_patient_get_waits_out_a_refusing_archive(monkeypatch):
    calls, waits = [], []

    def flaky(url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectError("refused")
        return response("ok")

    monkeypatch.setattr(src, "get", flaky)
    monkeypatch.setattr(src.time, "sleep", waits.append)
    assert src.patient_get("https://web.archive.org/x").text == "ok"
    assert waits == [60, 120]  # a minute, then two: the Archive recovers in minutes, not seconds
    monkeypatch.setattr(src, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    with pytest.raises(httpx.ConnectError):
        src.patient_get("https://web.archive.org/x", tries=2)
