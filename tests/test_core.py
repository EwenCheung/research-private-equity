import pytest

from pipeline.core import registry
from pipeline.core.companies import load_companies

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
