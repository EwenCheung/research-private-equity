import gzip
import json
from datetime import UTC, datetime

import pytest

from contracts import ContractError, validate
from pipeline.core import ROOT, registry
from pipeline.core.build import build
from pipeline.core.collect import collect
from pipeline.core.companies import load_companies
from pipeline.core.store import read_observations
from pipeline.sources import snapshots

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
    written, errors = collect(root=root, now=NOW)
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


def test_collect_rejects_rows_without_provenance(clean_registry, root):
    no_url = {k: v for k, v in ROW.items() if k != "source_url"}
    registry.source(**META)(lambda co: [no_url])
    written, errors = collect(root=root, now=NOW)
    assert written == {"fake_jobs": None}  # nothing invalid reaches disk
    assert len(errors) == 2 and "source_url" in errors[0]
    assert not (root / "data/raw").exists()


def test_a_failing_collector_does_not_stop_the_others(clean_registry, root):
    def boom(co):
        raise RuntimeError("api down")

    registry.source(**META)(boom)
    registry.source(**{**META, "id": "ok_jobs"})(lambda co: [{**ROW, "entity": co.slug}])
    written, errors = collect(root=root, now=NOW)
    assert written["fake_jobs"] is None and written["ok_jobs"].exists()
    assert "fake_jobs/a: RuntimeError: api down" in errors


def test_one_failing_company_keeps_the_other_companies_rows(clean_registry, root):
    def only_b(co):
        if co.slug == "a":
            raise RuntimeError("no board")
        return [{**ROW, "entity": "b"}]

    registry.source(**META)(only_b)
    written, errors = collect(root=root, now=NOW)
    assert [r["entity"] for r in raw_rows(written["fake_jobs"])] == ["b"]
    assert len(errors) == 1


def test_collect_filters(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "weekly_x", "cadence": "weekly"})(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "yipit_x", "method": "manual", "tier": "vendor"})(lambda co: 1 / 0)
    assert set(collect(root=root, now=NOW, cadence="daily")[0]) == {"fake_jobs"}  # manual never runs
    written, _ = collect(root=root, now=NOW, source_ids=["weekly_x"], company="b")
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


def test_ledger_rows_carry_who_and_evidence(clean_registry, root):
    meta = {**META, "id": "product_releases", "page": "product", "method": "ledger", "tier": "company-stated"}
    registry.source(**meta)(lambda co: [])
    registry.mart(id="product.releases", sources=["product_releases"])(open_roles)
    (root / "data/ledgers").mkdir(parents=True)
    (root / "data/ledgers/product_releases.csv").write_text(
        "as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence\n"
        "2026-09-01,a,open_roles,5,,https://example.com/post,EwenCheung,2026-09-02T10:00:00Z,blog post title\n"
    )
    spec = build_one(root, NOW)
    assert spec["sources"][0]["manual"] == {
        "entered_by": "EwenCheung",
        "entered_at": "2026-09-02T10:00:00Z",
        "evidence": "blog post title",
    }


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


def test_a_mart_only_sees_its_declared_sources(clean_registry, root):
    registry.source(**META)(lambda co: [{**ROW, "entity": co.slug}])
    registry.source(**{**META, "id": "other_jobs"})(lambda co: [{**ROW, "entity": co.slug, "value": 1}])
    registry.mart(id="hiring.open_roles", sources=["fake_jobs"])(open_roles)
    collect(root=root, now=NOW)
    assert [r["value"] for r in build_one(root, NOW)["rows"]] == [640]


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


# ---- snapshots ----


def test_appstore_snapshot_emits_ranks_for_tracked_apps_only(monkeypatch):
    feed = {"feed": {"entry": [{"id": {"attributes": {"im:id": i}}} for i in ("111", "6473753684", "222")]}}
    fake = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: feed})()
    monkeypatch.setattr(snapshots.httpx, "get", lambda *a, **k: fake)
    snapshots._chart.cache_clear()
    try:
        cos = load_companies(ROOT)
        rows = {c: list(snapshots.appstore_top_charts(cos[c])) for c in ("anthropic", "openai", "cohere")}
    finally:
        snapshots._chart.cache_clear()
    [claude] = rows["anthropic"]
    assert (claude["entity"], claude["metric"], claude["value"]) == ("anthropic", "appstore_rank", 2)
    assert claude["dims"]["app_name"] == "Claude"
    assert rows["openai"] == []  # tracked, but not in the chart: no row, never an invented rank
    assert rows["cohere"] == []  # no app configured
    validate(
        "observation",
        {
            **claude,
            "source": "appstore_top_charts",
            "method": "api",
            "tier": "platform",
            "retrieved_at": "2026-10-05T06:02:11Z",
        },
    )


def test_snapshot_source_is_declared_non_backfillable():
    meta = registry.SOURCES["appstore_top_charts"].meta
    assert meta["backfillable"] is False and meta["cadence"] == "daily"


def test_appstore_fetch_retries_a_stalled_request(monkeypatch):
    feed = {"feed": {"entry": [{"id": {"attributes": {"im:id": "9"}}}]}}
    ok = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: feed})()
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise snapshots.httpx.ReadTimeout("stalled")
        return ok

    monkeypatch.setattr(snapshots.httpx, "get", flaky)
    snapshots._chart.cache_clear()
    try:
        assert snapshots._chart() == {"9": 1}
    finally:
        snapshots._chart.cache_clear()
    assert len(calls) == 3
