import gzip
import json
import threading
import time
from datetime import UTC, datetime

import pytest

from contracts import ContractError, validate
from pipeline.core import ROOT, registry
from pipeline.core.build import build
from pipeline.core.collect import collect
from pipeline.core.companies import load_companies
from pipeline.core.http import SourceUnavailable
from pipeline.core.store import read_observations

META = {
    "id": "fake_jobs",
    "page": "hiring",
    "label": "Fake jobs API",
    "url": "https://example.com/{board}/jobs",
    "method": "api",
    "tier": "company-stated",
    "cadence": "daily",
    "sla_days": 2,
    "backfillable": False,
    "caveats": "Test source.",
}


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(registry, "SOURCES", {})
    monkeypatch.setattr(registry, "MARTS", {})


def test_source_metadata_is_validated_against_the_contract(clean_registry):
    with pytest.raises(Exception, match="sla_days"):
        registry.source(**{**META, "sla_days": 0})


def test_duplicate_ids_are_rejected(clean_registry):
    registry.source(**META)(lambda c: [])
    with pytest.raises(ValueError, match="duplicate source"):
        registry.source(**META)(lambda c: [])
    registry.mart(id="hiring.x", sources=["fake_jobs"])(lambda ctx: {})
    with pytest.raises(ValueError, match="duplicate mart"):
        registry.mart(id="hiring.x", sources=["fake_jobs"])(lambda ctx: {})


def test_discover_imports_every_module_in_a_package(clean_registry, tmp_path, monkeypatch):
    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(f"from pipeline.core import source\n@source(**{META!r})\ndef a(company): return []\n")
    monkeypatch.syspath_prepend(tmp_path)
    registry.discover(["fakepkg"])
    assert list(registry.SOURCES) == ["fake_jobs"]


def test_real_packages_are_discoverable():
    registry.discover()  # imports pipeline.sources.* and pipeline.marts.* for real


def test_company_config_and_identifiers(tmp_path):
    (tmp_path / "config/companies").mkdir(parents=True)
    (tmp_path / "config/identifiers").mkdir()
    (tmp_path / "config/companies/a.yaml").write_text("slug: a\nname: A\nrole: target\npeers: [b]\n")
    (tmp_path / "config/companies/b.yaml").write_text("slug: b\nname: B\nrole: peer\n")
    (tmp_path / "config/identifiers/hiring.yaml").write_text("a: {greenhouse: board-a}\n")
    cos = load_companies(tmp_path)
    assert cos["a"].peers == ("b",)
    assert cos["a"].ids("hiring") == {"greenhouse": "board-a"}
    assert cos["b"].ids("hiring") == {}  # page has no entry for this company
    assert cos["a"].ids("nope") == {}  # page has no identifiers file


def test_company_config_rejects_unknown_peer(tmp_path):
    (tmp_path / "config/companies").mkdir(parents=True)
    (tmp_path / "config/companies/a.yaml").write_text("slug: a\nname: A\nrole: target\npeers: [ghost]\n")
    with pytest.raises(ValueError, match="unknown peers"):
        load_companies(tmp_path)


def test_shipped_company_config_loads():
    cos = load_companies(ROOT)
    assert cos["anthropic"].role == "target"
    assert set(cos["anthropic"].peers) == {"openai", "google-deepmind", "xai", "mistral", "cohere"}


# ---- collect ----

NOW = datetime(2026, 10, 5, 6, 2, 11, tzinfo=UTC)
ROW = {
    "source_url": "https://example.com/a/jobs",
    "as_of": "2026-10-05",
    "entity": "a",
    "metric": "open_roles",
    "value": 640,
    "dims": {},
}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config/companies").mkdir(parents=True)
    (tmp_path / "config/companies/a.yaml").write_text("slug: a\nname: A\nrole: target\npeers: [b]\n")
    (tmp_path / "config/companies/b.yaml").write_text("slug: b\nname: B\nrole: peer\n")
    return tmp_path


def raw_rows(path):
    with gzip.open(path, "rt") as f:
        return [json.loads(line) for line in f]


def test_collect_stamps_provenance_and_writes_immutable_gzip(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    written, errors, _ = collect(root=root, now=NOW)
    assert errors == []
    path = written["fake_jobs"]
    assert path == root / "data/raw/fake_jobs/20261005T060211Z.jsonl.gz"
    rows = raw_rows(path)
    assert [r["entity"] for r in rows] == ["a", "b"]
    assert rows[0]["source"] == "fake_jobs"
    assert rows[0]["method"] == "api"
    assert rows[0]["tier"] == "company-stated"
    assert rows[0]["retrieved_at"] == "2026-10-05T06:02:11Z"
    with pytest.raises(FileExistsError):  # a raw file is never overwritten
        collect(root=root, now=NOW)


def test_collect_reports_progress_company_by_company_and_how_each_source_ended(clean_registry, root):
    def boom(co):
        raise RuntimeError("down")

    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "broken"})(boom)
    registry.source(**{**META, "id": "no_rows"})(lambda co: [])
    registry.source(**{**META, "id": "needs_key"})(lambda co: (_ for _ in ()).throw(SourceUnavailable("set KEY")))
    seen = []
    collect(root=root, now=NOW, on_progress=lambda *event: seen.append(event))
    assert [e for e in seen if e[0] == "fake_jobs"] == [
        ("fake_jobs", 0, 2, "running"),
        ("fake_jobs", 1, 2, "running"),
        ("fake_jobs", 2, 2, "running"),
        ("fake_jobs", 2, 2, "done"),
    ]
    ended = {e[0]: e[3] for e in seen if e[1] == e[2] and e[3] != "running"}
    assert ended == {"fake_jobs": "done", "broken": "failed", "no_rows": "empty", "needs_key": "skipped"}


def lane_sources(events):
    """Registers sources that record which others were running when they started. `barrier` makes two of them wait for each other."""
    running, lock = {}, threading.Lock()

    def make(sid, url, barrier=None):
        def fn(co):
            with lock:
                running[sid] = True
                events.append((sid, sorted(k for k, v in running.items() if v and k != sid)))
            if barrier:
                barrier.wait(5)  # only passes when both sources are running at the same moment
            time.sleep(0.02)
            with lock:
                running[sid] = False
            return [{**ROW, "entity": co.slug}]

        registry.source(**{**META, "id": sid, "url": url})(fn)

    return make


def test_with_workers_sources_on_different_services_run_side_by_side_and_on_one_service_one_after_another(
    clean_registry, root
):
    events, together = [], threading.Barrier(2)
    make = lane_sources(events)
    make("a1", "https://a.example.com/x")
    make("a2", "https://b.example.com/y")  # same service as a1: example.com
    make("solo1", "https://one.test/z", together)
    make("solo2", "https://two.test/z", together)
    written, errors, _ = collect(
        root=root, now=NOW, workers=4
    )  # a barrier that never fills would raise inside the sources
    assert list(written) == ["a1", "a2", "solo1", "solo2"] and errors == []  # source order, whatever finished first
    assert all("a2" not in others for sid, others in events if sid == "a1")  # never together with its lane-mate
    assert all("a1" not in others for sid, others in events if sid == "a2")


def test_workers_one_is_the_old_one_at_a_time(clean_registry, root):
    events = []
    make = lane_sources(events)
    make("solo1", "https://one.test/z")
    make("solo2", "https://two.test/z")
    collect(root=root, now=NOW, workers=1)
    assert all(others == [] for _, others in events)


def test_a_failure_in_one_lane_does_not_stop_the_others_and_results_keep_source_order(clean_registry, root):
    def boom(co):
        raise RuntimeError("down")

    registry.source(**{**META, "id": "broken", "url": "https://x.test/"})(boom)
    registry.source(**{**META, "id": "fine", "url": "https://y.test/"})(lambda co: [{**ROW, "entity": co.slug}])
    written, errors, _ = collect(root=root, now=NOW, workers=2)
    assert list(written) == ["broken", "fine"] and written["broken"] is None and written["fine"]
    assert [e.split(":")[0] for e in errors] == ["broken/a", "broken/b"]


def test_lanes_group_sources_by_the_service_they_call():
    from types import SimpleNamespace

    from pipeline.core.collect import lane

    def of(url, sid="s"):
        return lane(SimpleNamespace(id=sid, meta={"url": url}))

    assert of("https://efts.sec.gov/LATEST/search-index") == of("https://www.sec.gov/x") == "sec.gov"
    assert of("https://web.archive.org/web/x") == "archive.org"
    assert (
        of("https://{host}/api/v2/incidents.json", "status_incidents") == "status_incidents"
    )  # no real host: its own lane
    assert of("", "nourl") == "nourl"


def test_collect_rejects_rows_without_provenance(clean_registry, root):
    no_url = {k: v for k, v in ROW.items() if k != "source_url"}
    registry.source(**META)(lambda co: [no_url])
    written, errors, _ = collect(root=root, now=NOW)
    assert written == {"fake_jobs": None}  # nothing invalid reaches disk
    assert len(errors) == 2 and "source_url" in errors[0]
    assert not (root / "data/raw").exists()


def test_a_failing_collector_does_not_stop_the_others(clean_registry, root):
    def boom(co):
        raise RuntimeError("api down")

    registry.source(**META)(boom)
    registry.source(**{**META, "id": "ok_jobs"})(lambda co: [{**ROW, "entity": co.slug}])
    written, errors, _ = collect(root=root, now=NOW)
    assert written["fake_jobs"] is None and written["ok_jobs"].exists()
    assert "fake_jobs/a: RuntimeError: api down" in errors


def test_one_failing_company_keeps_the_other_companies_rows(clean_registry, root):
    def only_b(co):
        if co.slug == "a":
            raise RuntimeError("no board")
        return [{**ROW, "entity": "b"}]

    registry.source(**META)(only_b)
    written, errors, _ = collect(root=root, now=NOW)
    assert [r["entity"] for r in raw_rows(written["fake_jobs"])] == ["b"]
    assert len(errors) == 1


def test_collect_filters(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "weekly_x", "cadence": "weekly"})(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "yipit_x", "method": "manual", "tier": "vendor"})(lambda co: 1 / 0)
    assert set(collect(root=root, now=NOW, cadence="daily")[0]) == {"fake_jobs"}  # manual never runs
    written, _, _ = collect(root=root, now=NOW, source_ids=["weekly_x"], company="b")
    assert [r["entity"] for r in raw_rows(written["weekly_x"])] == ["b"]
    with pytest.raises(SystemExit):
        collect(root=root, now=NOW, source_ids=["nope"])


def test_hand_edited_raw_is_caught_on_read(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    path = collect(root=root, now=NOW)[0]["fake_jobs"]
    bad = {k: v for k, v in raw_rows(path)[0].items() if k != "retrieved_at"}
    with gzip.open(path, "wt") as f:
        f.write(json.dumps(bad) + "\n")
    with pytest.raises(ContractError, match="retrieved_at"):
        read_observations(root, META)


# ---- build ----

MART = {
    "title": "Open roles",
    "kind": "line",
    "encoding": {"x": {"field": "as_of", "type": "temporal"}, "y": {"field": "value", "type": "quantitative"}},
    "columns": [
        {"field": "as_of", "label": "Date", "format": "date"},
        {"field": "value", "label": "Roles", "format": "int"},
    ],
    "takeaway": [],
    "assumptions": [],
    "badges": [],
}


def open_roles(ctx):
    df = ctx.obs(metric="open_roles", entity="a")
    return {**MART, "rows": df[["as_of", "value"]].to_dict("records")}


def build_one(root, now):
    written, errors = build(root=root, now=now)
    assert errors == []
    return json.loads(written[0].read_text())


def test_build_writes_a_valid_mart_with_provenance(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    collect(root=root, now=NOW)
    spec = build_one(root, NOW)
    validate("chart_spec", spec)  # build already did; the file on disk must also be valid
    assert (spec["id"], spec["page"], spec["status"], spec["as_of"]) == (
        "hiring.open_roles",
        "hiring",
        "ok",
        "2026-10-05",
    )
    assert spec["rows"] == [{"as_of": "2026-10-05", "value": 640}]
    [src] = spec["sources"]
    assert (src["source"], src["freshness"], src["retrieved_at"], src["manual"]) == (
        "fake_jobs",
        "fresh",
        "2026-10-05T06:02:11Z",
        None,
    )
    assert src["url"] == "https://example.com/a/jobs"


@pytest.mark.parametrize(
    ("days_later", "state"),
    [(2, "fresh"), (3, "aging"), (4, "aging"), (5, "stale")],  # sla_days = 2
)
def test_build_freshness_follows_the_contract_rule(clean_registry, root, days_later, state):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    collect(root=root, now=NOW)
    later = NOW.replace(day=NOW.day + days_later)
    assert build_one(root, later)["sources"][0]["freshness"] == state


def test_build_with_no_data_is_awaiting_data_and_never_fresh(clean_registry, root):
    registry.source(**META)(lambda co: [])
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    spec = build_one(root, NOW)
    assert (spec["status"], spec["as_of"], spec["rows"]) == ("awaiting_data", None, [])
    assert (spec["sources"][0]["freshness"], spec["sources"][0]["retrieved_at"]) == ("never", None)


def test_build_writes_the_source_registry(clean_registry, root):
    """data/registry.json is what the API recomputes live freshness from, so it must list every declared source."""
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "idle_jobs"})(lambda co: [])
    collect(root=root, now=NOW)
    build(root=root, now=NOW)
    reg = json.loads((root / "data/registry.json").read_text())
    assert reg["generated_at"] == "2026-10-05T06:02:11Z"
    src = {s["id"]: s for s in reg["sources"]}
    assert set(src) == {"fake_jobs", "idle_jobs"}
    fake = src["fake_jobs"]
    assert {k: fake[k] for k in ("retrieved_at", "as_of", "row_count", "sla_days", "readable")} == {
        "retrieved_at": "2026-10-05T06:02:11Z",
        "as_of": "2026-10-05",
        "row_count": 2,
        "sla_days": 2,
        "readable": True,
    }
    assert (src["idle_jobs"]["retrieved_at"], src["idle_jobs"]["row_count"]) == (None, 0)
    assert fake["url"] == "https://example.com/a/jobs"
    assert src["idle_jobs"]["url"] == META["url"]


def test_ledger_rows_carry_who_and_evidence(clean_registry, root):
    meta = {**META, "id": "product_releases", "page": "product", "method": "ledger", "tier": "company-stated"}
    registry.source(**meta)(lambda co: [])
    registry.mart(id="product.releases", sources=["product_releases"])(open_roles)
    (root / "data/ledgers").mkdir(parents=True)
    (root / "data/ledgers/product_releases.csv").write_text(
        "as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence\n"
        "2026-09-01,a,open_roles,5,,https://example.com/old,EwenCheung,2026-09-02T10:00:00Z,old blog post\n"
        "2026-10-01,a,open_roles,6,,https://example.com/new,EwenCheung,2026-09-02T10:00:00Z,new blog post\n"
    )
    spec = build_one(root, NOW)
    assert spec["sources"][0]["manual"] == {
        "entered_by": "EwenCheung",
        "entered_at": "2026-09-02T10:00:00Z",
        "evidence": "new blog post",
    }
    assert spec["sources"][0]["url"] == "https://example.com/new"


def test_csv_row_without_evidence_is_rejected(clean_registry, root):
    meta = {**META, "id": "yipit_x", "method": "manual", "tier": "vendor"}
    registry.source(**meta)(lambda co: [])
    (root / "data/manual").mkdir(parents=True)
    (root / "data/manual/yipit_x.csv").write_text(
        "as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence\n"
        "2026-09-01,a,open_roles,5,,urn:yipit:x,EwenCheung,2026-09-02T10:00:00Z,\n"
    )
    with pytest.raises(ContractError, match=r"yipit_x.csv:2"):
        read_observations(root, meta)


def test_marts_get_company_display_names(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    seen = {}

    def named(ctx):
        seen.update(ctx.names)
        return open_roles(ctx)

    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(named)
    collect(root=root, now=NOW)
    build_one(root, NOW)
    assert seen == {"a": "A", "b": "B"}


def test_a_mart_only_sees_its_declared_sources(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "other_jobs"})(lambda co: [{**ROW, "entity": co.slug, "value": 1}])
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    collect(root=root, now=NOW)
    assert [r["value"] for r in build_one(root, NOW)["rows"]] == [640]


def test_the_source_link_is_a_row_the_chart_read_not_another_companys(clean_registry, root):
    def rows(co):  # b's rows are newer, so a link taken from the whole source would point at b
        day = "2026-10-05" if co.slug == "b" else "2026-10-04"
        return [{**ROW, "entity": co.slug, "as_of": day, "source_url": f"https://example.com/{co.slug}/jobs"}]

    registry.source(**META)(rows)
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)  # reads company a only
    collect(root=root, now=NOW)
    assert build_one(root, NOW)["sources"][0]["url"] == "https://example.com/a/jobs"


def test_a_broken_mart_is_reported_and_the_others_still_build(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.mart(id="hiring.bad", sources=["fake_jobs"])(lambda ctx: {"title": "no columns"})
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    collect(root=root, now=NOW)
    written, errors = build(root=root, now=NOW)
    assert [p.name for p in written] == ["hiring.open_roles.json"]
    assert len(errors) == 1 and errors[0].startswith("hiring.bad")


def test_duplicate_metric_ids_fail_the_build(clean_registry, root):
    (root / "config/metrics").mkdir(parents=True)
    spec = "m: {label: M, unit: count, definition: d, aggregation: last, higher_is: good}\n"
    (root / "config/metrics/hiring.yaml").write_text(spec)
    (root / "config/metrics/product.yaml").write_text(spec)
    with pytest.raises(ValueError, match="defined in both"):
        build(root=root, now=NOW)


# ---- shared http and incremental helpers ----


def test_a_source_without_its_key_is_skipped_not_failed(clean_registry, root):
    from pipeline.core.http import SourceUnavailable

    def needs_key(co):
        raise SourceUnavailable("set FAKE_KEY")

    registry.source(**META)(needs_key)
    registry.source(**{**META, "id": "ok_jobs"})(lambda co: [{**ROW, "entity": co.slug}])
    written, errors, skipped = collect(root=root, now=NOW)
    assert errors == [] and skipped == ["fake_jobs: set FAKE_KEY"]  # reported once, not per company
    assert written["fake_jobs"] is None and written["ok_jobs"].exists()


def test_latest_as_of_reads_what_is_already_stored(clean_registry, root):
    from pipeline.core.store import latest_as_of

    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug, "as_of": d} for d in ("2026-10-01", "2026-10-03")])
    collect(root=root, now=NOW)
    assert latest_as_of(root, "fake_jobs", "a") == "2026-10-03"
    assert latest_as_of(root, "fake_jobs", "ghost") is None
    assert latest_as_of(root, "never_collected", "a") is None
    assert latest_as_of(root, "fake_jobs", "a", package="x") is None  # dims filter: no row has that package


def test_http_retries_transient_errors_then_succeeds(monkeypatch):
    import httpx

    from pipeline.core import http

    calls = []

    def fake(method, url, **kw):
        calls.append(kw["headers"]["User-Agent"])
        status = 503 if len(calls) < 3 else 200
        return httpx.Response(status, request=httpx.Request(method, url))

    monkeypatch.setattr(http.httpx, "request", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.get("https://example.com").status_code == 200
    assert len(calls) == 3 and calls[0] == http.UA


def test_sec_requires_a_user_agent(monkeypatch):
    from pipeline.core import http

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(http.SourceUnavailable, match="SEC_USER_AGENT"):
        http.sec_get("https://efts.sec.gov/LATEST/search-index")


def test_nan_in_mart_rows_is_written_as_null(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])

    def with_gap(ctx):
        ctx.obs(metric="open_roles")
        return {**MART, "rows": [{"as_of": "2026-10-05", "value": float("nan")}]}

    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(with_gap)
    collect(root=root, now=NOW)
    written, errors = build(root=root, now=NOW)
    assert errors == []
    assert json.loads(written[0].read_text())["rows"] == [{"as_of": "2026-10-05", "value": None}]


def test_rows_before_a_failure_are_kept_and_the_error_reported(clean_registry, root):
    def flaky(co):
        yield {**ROW, "entity": co.slug, "as_of": "2026-10-01"}
        raise RuntimeError("rate limited")

    registry.source(**META)(flaky)
    written, errors, _ = collect(root=root, now=NOW)
    assert [r["as_of"] for r in raw_rows(written["fake_jobs"])] == ["2026-10-01", "2026-10-01"]  # one per company
    assert errors == [
        f"fake_jobs/{c}: RuntimeError: rate limited (kept 1 rows collected before it)" for c in ("a", "b")
    ]


@pytest.mark.parametrize(
    ("status", "headers", "wait"),
    [
        (403, {"Retry-After": "60"}, 60.0),  # GitHub secondary rate limit
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}, None),  # primary: wait until reset
        (403, {}, None),  # a real refusal
        (403, {"_body": "You have exceeded a secondary rate limit."}, 120.0),  # GitHub: body only, no header
        (429, {}, 30.0),
        (500, {}, None),
    ],
)
def test_rate_limit_wait(status, headers, wait):
    import httpx

    from pipeline.core.http import rate_limit_wait

    got = rate_limit_wait(httpx.Response(status, headers=headers, text=headers.pop("_body", "")))
    if headers.get("x-ratelimit-reset"):
        assert got > 1000
    else:
        assert got == wait
