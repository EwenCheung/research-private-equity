"""Metric dictionary: merges config/metrics/*.yaml; a metric id defined twice is an error."""

from pathlib import Path

import yaml

FIELDS = {"label", "unit", "definition", "aggregation", "higher_is"}


def load_metrics(root: Path) -> dict[str, dict]:
    metrics, owner = {}, {}
    for path in sorted((root / "config" / "metrics").glob("*.yaml")):
        for mid, spec in (yaml.safe_load(path.read_text()) or {}).items():
            if mid in metrics:
                raise ValueError(f"metric {mid!r} defined in both {owner[mid]} and {path.name}")
            if missing := FIELDS - set(spec):
                raise ValueError(f"{path.name}: metric {mid!r} is missing {sorted(missing)}")
            metrics[mid], owner[mid] = spec, path.name
    return metrics
