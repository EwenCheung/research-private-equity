"""@source and @mart decorators, plus the auto-discovering registry (no shared file to edit)."""

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

from contracts import validate

PACKAGES = ("pipeline.sources", "pipeline.marts")
AUTOMATED = ("api", "scrape")  # manual/ledger sources are CSV-fed; collect never calls them


@dataclass(frozen=True)
class Source:
    meta: dict
    fn: Callable

    @property
    def id(self) -> str:
        return self.meta["id"]


@dataclass(frozen=True)
class Mart:
    id: str
    sources: tuple[str, ...]
    fn: Callable

    @property
    def page(self) -> str:
        return self.id.split(".")[0]


SOURCES: dict[str, Source] = {}
MARTS: dict[str, Mart] = {}


def source(**meta):
    """Declare a collector next to its code; metadata is checked against contracts/source.schema.json."""
    validate("source", meta)

    def register(fn):
        if meta["id"] in SOURCES:
            raise ValueError(f"duplicate source id {meta['id']!r}")
        SOURCES[meta["id"]] = Source(meta, fn)
        return fn

    return register


def mart(*, id: str, sources: list[str]):
    """Declare a mart: the ids of the sources it may read, and a function returning the chart content."""
    if not sources:
        raise ValueError(f"mart {id!r} must declare at least one source")

    def register(fn):
        if id in MARTS:
            raise ValueError(f"duplicate mart id {id!r}")
        MARTS[id] = Mart(id, tuple(sources), fn)
        return fn

    return register


def discover(packages=PACKAGES) -> None:
    """Import every module in the given packages so their decorators run."""
    for name in packages:
        pkg = importlib.import_module(name)
        for mod in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"{name}.{mod.name}")
