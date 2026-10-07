"""What the API serves, read from the offline SQLite file that `python -m pipeline.offline pack` writes (schema version 1).

The file holds the built charts and the registry, so a machine with no internet needs no data/ folder and no build: point
`DATA_DB` at the file and the dashboard shows what was collected when it was packed.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1


class DbStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"DATA_DB {self.path} does not exist")
        with self._open() as con:
            if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} was written with another schema version (expected {SCHEMA_VERSION})")

    def _open(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)

    def charts(self) -> list[dict]:
        with self._open() as con:
            return [json.loads(text) for (text,) in con.execute("SELECT spec FROM charts ORDER BY id")]

    def chart(self, chart_id: str) -> dict | None:
        with self._open() as con:
            row = con.execute("SELECT spec FROM charts WHERE id = ?", (chart_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def registry(self) -> dict | None:
        with self._open() as con:
            row = con.execute("SELECT content FROM files WHERE kind = 'registry'").fetchone()
        return json.loads(row[0]) if row else None
