"""SQLite persistence for raw replays.

Raw ballchasing JSON is the source of truth; ``Match`` objects are derived on
read (see ``parse.py``). That keeps sync incremental — only unseen replay ids
are ever downloaded — and lets parser improvements apply retroactively.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS replays (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    playlist_id TEXT,
    fetched_at  TEXT NOT NULL,
    raw         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS replays_date ON replays(date);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ReplayStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ReplayStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def known_ids(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT id FROM replays")}

    def upsert(self, replay: dict[str, Any]) -> None:
        self.upsert_many([replay])

    def upsert_many(self, replays: Iterable[dict[str, Any]]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [
            (str(r["id"]), str(r.get("date") or r.get("created") or ""), r.get("playlist_id"), now, json.dumps(r))
            for r in replays
        ]
        self.conn.executemany(
            "INSERT INTO replays(id, date, playlist_id, fetched_at, raw) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET date=excluded.date, playlist_id=excluded.playlist_id, "
            "fetched_at=excluded.fetched_at, raw=excluded.raw",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def iter_raw(self) -> Iterator[dict[str, Any]]:
        for (raw,) in self.conn.execute("SELECT raw FROM replays ORDER BY date"):
            yield json.loads(raw)

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM replays").fetchone()[0])

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
