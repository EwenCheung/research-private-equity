import gzip
import json
from datetime import UTC, datetime

import pytest

from contracts import ContractError
from pipeline.core import registry
from pipeline.core.collect import collect
from pipeline.core.companies import load_companies
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
    from pipeline.core import ROOT

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
