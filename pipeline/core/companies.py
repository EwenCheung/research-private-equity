"""Company config loader: config/companies/<slug>.yaml, plus per-page identifiers from config/identifiers/<page>.yaml."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml


@cache
def _identifiers(root: Path, page: str) -> dict:
    path = root / "config" / "identifiers" / f"{page}.yaml"
    return yaml.safe_load(path.read_text()) or {} if path.exists() else {}


@dataclass(frozen=True)
class Company:
    slug: str
    name: str
    role: str
    peers: tuple[str, ...]
    root: Path

    def ids(self, page: str) -> dict:
        """How this company is found in the given page's sources; {} when the page has no entry for it."""
        return _identifiers(self.root, page).get(self.slug) or {}


def load_companies(root: Path) -> dict[str, Company]:
    companies = {}
    for path in sorted((root / "config" / "companies").glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text())
        if cfg.get("slug") != path.stem:
            raise ValueError(f"{path.name}: slug must equal the file name")
        if cfg.get("role") not in ("target", "peer"):
            raise ValueError(f"{path.name}: role must be 'target' or 'peer'")
        if not cfg.get("name"):
            raise ValueError(f"{path.name}: name is required")
        companies[cfg["slug"]] = Company(cfg["slug"], cfg["name"], cfg["role"], tuple(cfg.get("peers") or ()), root)
    for c in companies.values():
        if c.peers and c.role != "target":
            raise ValueError(f"{c.slug}: only a target lists peers")
        if missing := [p for p in c.peers if p not in companies]:
            raise ValueError(f"{c.slug}: unknown peers {missing}")
    return companies
