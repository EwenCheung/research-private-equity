import json
from datetime import UTC, datetime

from pipeline import report

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def src(sid, retrieved, rows, sla=2, method="api", readable=True, as_of="2026-10-04"):
    return {
        "id": sid,
        "method": method,
        "sla_days": sla,
        "retrieved_at": retrieved,
        "as_of": as_of,
        "row_count": rows,
        "readable": readable,
    }


def make_root(tmp_path, sources, marts=()):
    (tmp_path / "data" / "marts").mkdir(parents=True)
    (tmp_path / "data" / "registry.json").write_text(
        json.dumps({"generated_at": "2026-10-05T11:00:00Z", "sources": sources})
    )
    for mart in marts:
        (tmp_path / "data" / "marts" / f"{mart['id']}.json").write_text(json.dumps(mart))
    return tmp_path


def test_freshness_is_recomputed_against_the_clock_not_trusted_from_the_build(tmp_path):
    root = make_root(
        tmp_path,
        [
            src("fresh_one", "2026-10-05T06:00:00Z", 10),
            src("aging_one", "2026-10-01T12:00:00Z", 5),
            src("stale_one", "2026-09-20T00:00:00Z", 5),
            src("never_one", None, 0, as_of=None),
        ],
    )
    rep = report.build_report(root, NOW, before={"sources": []})
    assert {s["id"]: s["freshness"] for s in rep["sources"]} == {
        "fresh_one": "fresh",
        "aging_one": "aging",
        "stale_one": "stale",
        "never_one": "never",
    }
    assert [s["id"] for s in rep["needs_attention"]] == ["stale_one", "never_one"]  # aging is not yet a problem


def test_row_changes_compare_with_the_previous_registry(tmp_path):
    root = make_root(tmp_path, [src("a", "2026-10-05T06:00:00Z", 110), src("b", "2026-10-05T06:00:00Z", 5)])
    rep = report.build_report(root, NOW, before={"sources": [src("a", "2026-10-04T06:00:00Z", 100)]})
    assert {s["id"]: s["rows_added"] for s in rep["sources"]} == {"a": 10, "b": None}  # b is new: no change to report
    assert "rows 110 (+10)" in report.render(rep)


def test_unreadable_sources_need_attention_even_when_recent(tmp_path):
    root = make_root(tmp_path, [src("broken", "2026-10-05T06:00:00Z", 0, readable=False)])
    rep = report.build_report(root, NOW, before={"sources": []})
    text = report.render(rep)
    assert rep["needs_attention"] and "unreadable: stored rows failed validation" in text


def test_each_kind_of_source_gets_the_right_next_step(tmp_path):
    root = make_root(
        tmp_path,
        [
            src("api_x", None, 0, method="api"),
            src("hand_x", None, 0, method="manual"),
            src("ledger_x", None, 0, method="ledger"),
        ],
    )
    text = report.render(report.build_report(root, NOW, before={"sources": []}))
    assert "pipeline.collect --source api_x" in text and "/add-manual-data" in text and "verbatim quote" in text


def test_empty_charts_are_listed_with_the_reason(tmp_path):
    awaiting = {"id": "licensed_data.x", "status": "awaiting_data", "takeaway": ["Awaiting data: nothing entered."]}
    ok = {"id": "hiring.y", "status": "ok", "takeaway": ["fine"]}
    root = make_root(tmp_path, [src("a", "2026-10-05T06:00:00Z", 1)], [awaiting, ok])
    rep = report.build_report(root, NOW, before={"sources": []})
    assert rep["empty_charts"] == [{"chart": "licensed_data.x", "why": "Awaiting data: nothing entered."}]
    assert "1 charts are awaiting data" in report.render(rep)


def test_a_healthy_registry_reports_nothing_to_do(tmp_path):
    root = make_root(tmp_path, [src("a", "2026-10-05T06:00:00Z", 1)])
    rep = report.build_report(root, NOW, before={"sources": []})
    assert rep["needs_attention"] == [] and "Nothing needs attention" in report.render(rep)


def test_without_git_history_it_says_changes_are_unknown(tmp_path):
    root = make_root(tmp_path, [src("a", "2026-10-05T06:00:00Z", 1)])
    assert report.previous(root) is None  # not a git repo
    assert "No committed registry to compare with" in report.render(report.build_report(root, NOW))
