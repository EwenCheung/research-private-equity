"""build CLI: raw + manual + ledgers -> data/marts/<page>.<chart>.json, every mart validated as a chart spec."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from contracts import freshness, validate
from pipeline.core import ROOT, registry
from pipeline.core.companies import load_companies
from pipeline.core.metrics import load_metrics
from pipeline.core.store import read_observations

COLUMNS = ["source", "source_url", "method", "as_of", "retrieved_at", "tier", "entity", "metric", "value", "dims"]
COLUMNS += ["entered_by", "evidence"]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ctx:
    """What a mart sees: only the observations of the sources it declared, plus company display names.

    Label series with ctx.names[slug] ("Anthropic", not "anthropic") so every chart names companies the same way.
    """

    def __init__(self, rows: list[dict], names: dict[str, str] | None = None):
        self.df = pd.DataFrame(rows, columns=COLUMNS)
        self.names = names or {}
        self.used: set[int] = set()  # index of every observation the mart read, for as_of

    def obs(self, **filters) -> pd.DataFrame:
        """Observations filtered by column; a list value means 'any of'. e.g. ctx.obs(metric='open_roles')."""
        df = self.df
        for col, want in filters.items():
            df = df[df[col].isin(want if isinstance(want, list | tuple | set) else [want])]
        self.used |= set(df.index)
        return df


def _source_refs(ctx: Ctx, source_ids, now: datetime) -> list[dict]:
    refs = []
    for sid in source_ids:
        meta = registry.SOURCES[sid].meta
        mine = ctx.df[ctx.df.source == sid]
        used = mine[mine.index.isin(ctx.used)]
        latest = max(mine.itertuples(), key=lambda r: datetime.fromisoformat(r.retrieved_at), default=None)
        retrieved = datetime.fromisoformat(latest.retrieved_at) if latest else None
        manual = None
        if latest and meta["method"] in ("manual", "ledger"):
            manual = {"entered_by": latest.entered_by, "entered_at": _iso(retrieved), "evidence": latest.evidence}
        refs.append(
            {
                "source": sid,
                "label": meta["label"],
                "url": meta["url"],
                "method": meta["method"],
                "tier": meta["tier"],
                "cadence": meta["cadence"],
                "as_of": used.as_of.max() if len(used) else None,
                "retrieved_at": _iso(retrieved) if retrieved else None,
                "freshness": freshness(retrieved, meta["sla_days"], now),
                "manual": manual,
            }
        )
    return refs


def build_mart(m: registry.Mart, all_rows: dict[str, list[dict]], now: datetime, names: dict | None = None) -> dict:
    missing = [s for s in m.sources if s not in registry.SOURCES]
    if missing:
        raise ValueError(f"mart {m.id} declares unknown sources {missing}")
    ctx = Ctx([r for s in m.sources for r in all_rows[s]], names)
    spec = dict(m.fn(ctx))
    used = ctx.df.loc[sorted(ctx.used)]
    empty = not spec.get("rows")
    spec |= {
        "id": m.id,
        "page": m.page,
        "as_of": None if empty or used.empty else used.as_of.max(),
        "sources": _source_refs(ctx, m.sources, now),
        "status": "awaiting_data" if empty else "ok",
        "generated_at": _iso(now),
    }
    return validate("chart_spec", spec)


def write_registry(root: Path, all_rows: dict[str, list[dict] | None], now: datetime) -> Path:
    """data/registry.json: every declared source with its latest retrieval, latest data date and row count.

    The API recomputes freshness from this at request time, so a stalled pipeline turns badges amber/red
    without waiting for a rebuild.
    """
    sources = []
    for sid, s in sorted(registry.SOURCES.items()):
        rows = all_rows.get(sid) or []
        latest = max(rows, key=lambda r: datetime.fromisoformat(r["retrieved_at"]), default=None)
        sources.append(
            {
                **s.meta,
                "retrieved_at": _iso(datetime.fromisoformat(latest["retrieved_at"])) if latest else None,
                "as_of": max(r["as_of"] for r in rows) if rows else None,
                "row_count": len(rows),
                "readable": all_rows.get(sid) is not None,  # False: stored rows failed validation (see build errors)
            }
        )
    out = root / "data" / "registry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": _iso(now), "sources": sources}, indent=2) + "\n", encoding="utf-8")
    return out


def build(*, root: Path = ROOT, now: datetime | None = None) -> tuple[list[Path], list[str]]:
    """Return (mart files written, errors). One failing mart never stops the others. Always refreshes the registry."""
    now = now or datetime.now(UTC)
    registry.discover()
    load_metrics(root)  # fails on a metric id defined in two files
    names = {c.slug: c.name for c in load_companies(root).values()}
    all_rows, errors, written = {}, [], []
    for sid, s in registry.SOURCES.items():
        try:
            all_rows[sid] = read_observations(root, s.meta)
        except ValueError as e:  # includes ContractError
            errors.append(f"{sid}: {e}")
            all_rows[sid] = None
    write_registry(root, all_rows, now)
    for m in registry.MARTS.values():
        try:
            if bad := [s for s in m.sources if s in all_rows and all_rows[s] is None]:
                raise ValueError(f"unreadable sources {bad}")
            spec = build_mart(m, all_rows, now, names)
            out = root / "data" / "marts" / f"{m.id}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written.append(out)
        except Exception as e:  # noqa: BLE001 - one broken mart must not stop the others
            errors.append(f"{m.id}: {type(e).__name__}: {e}")
    return written, errors


def main(argv=None) -> int:
    written, errors = build()
    print("wrote data/registry.json")
    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errors else 0
