"""Raw observation storage: immutable gzip JSONL for collected sources, CSV for manual and ledger sources."""

import csv
import gzip
import json
from datetime import datetime
from pathlib import Path

from contracts import ContractError, validate

FILE_STAMP = "%Y%m%dT%H%M%SZ"
CSV_FIELDS = ("as_of", "entity", "metric", "source_url", "entered_by", "retrieved_at", "evidence")


def raw_dir(root: Path, source_id: str) -> Path:
    return root / "data" / "raw" / source_id


def write_raw(root: Path, source_id: str, rows: list[dict], now: datetime) -> Path:
    """Write one run's rows as data/raw/<source>/<YYYYMMDDTHHMMSSZ>.jsonl.gz. Never overwrites ("x" mode)."""
    path = raw_dir(root, source_id) / f"{now.strftime(FILE_STAMP)}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "xt", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return path


def _raw_rows(root: Path, source_id: str) -> list[dict]:
    rows = []
    for path in sorted(raw_dir(root, source_id).glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                try:
                    rows.append(validate("observation", json.loads(line)))
                except (ContractError, ValueError) as e:  # a raw file edited by hand is a data-integrity error
                    raise ContractError(f"{path.relative_to(root)}:{n}: {e}") from e
    return rows


def _csv_rows(root: Path, meta: dict) -> list[dict]:
    folder = "manual" if meta["method"] == "manual" else "ledgers"  # ledger ids are <page>_<name>
    path = root / "data" / folder / f"{meta['id']}.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            try:
                value = float(rec["value"])
                row = {k: rec[k] for k in CSV_FIELDS}
                row |= {
                    "source": meta["id"],
                    "method": meta["method"],
                    "tier": meta["tier"],
                    "value": int(value) if value.is_integer() else value,
                    "dims": json.loads(rec["dims"] or "{}"),
                }
                rows.append(validate("observation", row))
            except (ContractError, ValueError, KeyError) as e:
                raise ContractError(f"{path.relative_to(root)}:{reader.line_num}: {e!r}") from e
    return rows


def latest_as_of(root: Path, source_id: str, entity: str) -> str | None:
    """Newest as_of already stored for one entity, so an incremental collector fetches only what is new."""
    return max((r["as_of"] for r in _raw_rows(root, source_id) if r["entity"] == entity), default=None)


def read_observations(root: Path, meta: dict) -> list[dict]:
    """Every stored observation of one source. ponytail: validates all rows on every read; cache once raw passes ~1M rows."""
    return _csv_rows(root, meta) if meta["method"] in ("manual", "ledger") else _raw_rows(root, meta["id"])
