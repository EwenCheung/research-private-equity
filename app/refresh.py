"""The refresh button's job: fetch every source again and rebuild the SQLite file, one run at a time, and remember how it went.

The work itself is a function `work(db) -> report`. It returns a dict with at least `state` (ok, partial, unchanged, offline or failed),
`swapped` (did the file change) and `message`. It must leave the file alone unless the new data is complete enough to replace it;
the dashboard keeps reading the old file the whole time. The last report is kept next to the file, so it survives a restart.
"""

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Refresher:
    # ponytail: one job per process (Render runs one); a lock file would make it hold across workers.
    def __init__(self, db: Path, work: Callable[[Path], dict]):
        self.db, self.work = db, work
        self.note = db.with_name(db.name + ".refresh.json")
        self.started_at: str | None = None
        self.last: dict | None = None
        self._lock = threading.Lock()
        try:
            self.last = json.loads(self.note.read_text())
        except (OSError, ValueError):
            pass

    def start(self) -> bool:
        """Begin a refresh in the background; False when one is already running."""
        if not self._lock.acquire(blocking=False):
            return False
        self.started_at = now()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            try:
                report = self.work(self.db)
            except (Exception, SystemExit) as e:  # noqa: BLE001 - a collector or the build may exit; the job must still end
                report = {
                    "state": "failed",
                    "swapped": False,
                    "message": f"The refresh stopped ({type(e).__name__}: {e}). The dashboard keeps the data it had.",
                }
            self.last = {**report, "started_at": self.started_at, "finished_at": now()}
            try:
                self.note.write_text(json.dumps(self.last, indent=2))
            except OSError:
                pass  # read-only folder: the report stays in memory until a restart
        finally:
            self.started_at = None
            self._lock.release()

    def status(self) -> dict:
        return {"running": self.started_at is not None, "started_at": self.started_at, "last": self.last}
