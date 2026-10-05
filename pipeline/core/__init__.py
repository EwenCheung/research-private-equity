"""Shared pipeline core: registry, company config, raw store, collect and build."""

from pathlib import Path

from pipeline.core.registry import mart, source

ROOT = Path(__file__).resolve().parents[2]

__all__ = ["ROOT", "mart", "source"]
