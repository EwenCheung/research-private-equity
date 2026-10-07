"""Contract schemas, validators and the freshness rule, shared by the pipeline, the API and tests."""

import json
from datetime import datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

DIR = Path(__file__).parent
KINDS = ("observation", "source", "chart_spec", "ai_report")


class ContractError(ValueError):
    pass


@cache
def _validator(kind: str) -> Draft202012Validator:
    if kind not in KINDS:
        raise ValueError(f"unknown contract {kind!r}; expected one of {KINDS}")
    schema = json.loads((DIR / f"{kind}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(kind: str, obj: dict) -> dict:
    """Return obj unchanged when it satisfies the contract; otherwise raise ContractError listing every violation."""
    errors = [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in _validator(kind).iter_errors(obj)
    ]
    if kind == "chart_spec" and not errors:
        errors = _chart_invariants(obj)
    if errors:
        name = obj.get("id") or obj.get("source") or obj.get("reporter") or ""
        raise ContractError(f"{kind} {name}: " + "; ".join(errors))
    return obj


def _chart_invariants(c: dict) -> list[str]:
    """Rules JSON Schema cannot express."""
    errs = []
    if not c["id"].startswith(c["page"] + "."):
        errs.append(f"id must start with '{c['page']}.'")
    fields = {col["field"] for col in c["columns"]}
    for name, channel in c["encoding"].items():
        if channel["field"] not in fields:
            errs.append(f"encoding.{name}.field '{channel['field']}' is not a column")
    for i, panel in enumerate(c.get("panels", [])):
        if "x" in panel and panel["x"]["field"] not in fields:
            errs.append(f"panels/{i}.x.field '{panel['x']['field']}' is not a column")
    for i, layer in enumerate(c.get("layers", [])):
        if layer.get("panel", 0) > len(c.get("panels", [])):
            errs.append(
                f"layers/{i}.panel {layer['panel']} has no panel {layer['panel']} (there are {1 + len(c.get('panels', []))})"
            )
        needs = {"band": ("y_low", "y_high"), "rule": ("label",)}.get(layer["mark"], ("y",))
        for key in needs:
            if key not in layer:
                errs.append(f"layers/{i} ({layer['mark']}) needs {key}")
        for key in ("y", "y_low", "y_high", "label", "series"):
            if key in layer and layer[key] not in fields:
                errs.append(f"layers/{i}.{key} '{layer[key]}' is not a column")
    for i, row in enumerate(c["rows"]):
        if missing := fields - row.keys():
            errs.append(f"rows/{i} missing {sorted(missing)}")
    empty = not c["rows"]
    if (c["status"] == "awaiting_data") != empty:
        errs.append("status must be 'awaiting_data' exactly when rows is empty")
    if (c["as_of"] is None) != empty:
        errs.append("as_of must be null exactly when rows is empty")
    for s in c["sources"]:
        if (s["freshness"] == "never") != (s["retrieved_at"] is None):
            errs.append(f"source {s['source']}: freshness is 'never' exactly when retrieved_at is null")
        needs_manual = s["method"] in ("manual", "ledger") and s["retrieved_at"] is not None
        if needs_manual != (s["manual"] is not None):
            errs.append(f"source {s['source']}: 'manual' block required exactly for manual/ledger sources with rows")
    return errs


def freshness(retrieved_at: datetime | None, sla_days: int, now: datetime) -> str:
    """fresh if age <= sla_days, aging if <= 2 x sla_days, stale beyond, never if nothing was ever retrieved."""
    if retrieved_at is None:
        return "never"
    age_days = (now - retrieved_at).total_seconds() / 86400
    if age_days <= sla_days:
        return "fresh"
    return "aging" if age_days <= 2 * sla_days else "stale"
