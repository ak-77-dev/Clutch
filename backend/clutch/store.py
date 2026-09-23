"""SQLite storage shared by every game.

Raw upstream match JSON is the source of truth (parsed on read), so adapter
improvements apply retroactively and sync only ever downloads unseen matches.
A match is stored once and linked to every tracked player who appears in it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clutch.models import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    game        TEXT NOT NULL,
    key         TEXT NOT NULL,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    synced_at   TEXT,
    PRIMARY KEY (game, key)
);
CREATE TABLE IF NOT EXISTS aliases (
    game   TEXT NOT NULL,
    query  TEXT NOT NULL,
    key    TEXT NOT NULL,
    PRIMARY KEY (game, query)
);
CREATE TABLE IF NOT EXISTS matches (
    game      TEXT NOT NULL,
    match_id  TEXT NOT NULL,
    date      TEXT NOT NULL,
    raw       TEXT NOT NULL,
    PRIMARY KEY (game, match_id)
);
CREATE TABLE IF NOT EXISTS player_matches (
    game        TEXT NOT NULL,
    player_key  TEXT NOT NULL,
    match_id    TEXT NOT NULL,
    PRIMARY KEY (game, player_key, match_id)
);
CREATE INDEX IF NOT EXISTS matches_date ON matches(game, date);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI runs sync endpoints in a thread pool: one connection, serialized.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            self._conn.commit()
            return rows

    # ── profiles ─────────────────────────────────────────────────────────────
    def save_profile(self, profile: Profile, *queries: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO profiles(game, key, data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(game, key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (profile.game, profile.key, json.dumps(profile.to_dict()), _now()),
            )
            for q in queries:
                self._conn.execute(
                    "INSERT INTO aliases(game, query, key) VALUES (?, ?, ?) ON CONFLICT(game, query) DO UPDATE SET key=excluded.key",
                    (profile.game, normalize_query(q), profile.key),
                )
            self._conn.commit()

    def profile_by_query(self, game: str, query: str) -> Profile | None:
        rows = self._exec(
            "SELECT p.data FROM aliases a JOIN profiles p ON p.game = a.game AND p.key = a.key WHERE a.game = ? AND a.query = ?",
            (game, normalize_query(query)),
        )
        return Profile(**json.loads(rows[0][0])) if rows else None

    def profile(self, game: str, key: str) -> Profile | None:
        rows = self._exec("SELECT data FROM profiles WHERE game = ? AND key = ?", (game, key))
        return Profile(**json.loads(rows[0][0])) if rows else None

    def profile_updated_at(self, game: str, key: str) -> str | None:
        rows = self._exec("SELECT updated_at FROM profiles WHERE game = ? AND key = ?", (game, key))
        return rows[0][0] if rows else None

    def mark_synced(self, game: str, key: str) -> str:
        now = _now()
        self._exec("UPDATE profiles SET synced_at = ? WHERE game = ? AND key = ?", (now, game, key))
        return now

    def synced_at(self, game: str, key: str) -> str | None:
        rows = self._exec("SELECT synced_at FROM profiles WHERE game = ? AND key = ?", (game, key))
        return rows[0][0] if rows else None

    def recent_profiles(self, limit: int = 8) -> list[Profile]:
        rows = self._exec("SELECT data FROM profiles ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [Profile(**json.loads(r[0])) for r in rows]

    # ── matches ──────────────────────────────────────────────────────────────
    def known_match_ids(self, game: str, player_key: str) -> set[str]:
        rows = self._exec("SELECT match_id FROM player_matches WHERE game = ? AND player_key = ?", (game, player_key))
        return {r[0] for r in rows}

    def add_matches(self, game: str, player_key: str, matches: Iterable[tuple[str, str, dict[str, Any]]]) -> int:
        """``matches`` = (match_id, iso_date, raw_json)."""
        n = 0
        with self._lock:
            for match_id, date, raw in matches:
                self._conn.execute(
                    "INSERT INTO matches(game, match_id, date, raw) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(game, match_id) DO UPDATE SET raw=excluded.raw, date=excluded.date",
                    (game, match_id, date, json.dumps(raw)),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO player_matches(game, player_key, match_id) VALUES (?, ?, ?)",
                    (game, player_key, match_id),
                )
                n += 1
            self._conn.commit()
        return n

    def raw_matches(self, game: str, player_key: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT m.raw FROM player_matches pm JOIN matches m ON m.game = pm.game AND m.match_id = pm.match_id "
            "WHERE pm.game = ? AND pm.player_key = ? ORDER BY m.date",
            (game, player_key),
        )
        return [json.loads(r[0]) for r in rows]

    def raw_match(self, game: str, match_id: str) -> dict[str, Any] | None:
        rows = self._exec("SELECT raw FROM matches WHERE game = ? AND match_id = ?", (game, match_id))
        return json.loads(rows[0][0]) if rows else None
