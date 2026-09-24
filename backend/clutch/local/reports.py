"""Session report cards: what happened in a play session, built when the game closes.

A card always has the time played and the clips saved. When the game's stats
account is linked, it's filled in again after the post-session sync: the
matches played in that window, wins and losses, the best game, and how the
session's form compares with the player's usual numbers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER,
    game_id     TEXT NOT NULL,
    game_name   TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_time ON reports(ended_at);
"""


def iso_ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def match_window(matches: list[Any], started_at: float, ended_at: float, slack: float = 90) -> list[Any]:
    """Matches that were played during a session (``Match`` objects, any game)."""
    return [m for m in matches if started_at - slack <= iso_ts(m.date) <= ended_at + slack]


def summarize_matches(matches: list[Any], meta: Any, baseline: list[Any]) -> dict[str, Any] | None:
    """W/L, the best game by the game's first headline stat, and how the session compared with usual."""
    counted = [m for m in matches if m.result != "remake"]
    if not counted:
        return None
    key = meta.kpis[0]
    metric = meta.metric(key)
    better = max if metric.higher_is_better else min
    scored = [m for m in counted if m.metrics.get(key) is not None]
    best = better(scored, key=lambda m: m.metrics[key]) if scored else None

    def mean(ms: list[Any]) -> float | None:
        vals = [m.metrics[key] for m in ms if m.metrics.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    session_avg, usual = mean(counted), mean([m for m in baseline if m.result != "remake"])
    return {
        "games": len(counted),
        "wins": sum(m.result == "win" for m in counted),
        "losses": sum(m.result == "loss" for m in counted),
        "metric": {"key": key, "label": metric.label, "fmt": metric.fmt, "session": session_avg, "usual": usual},
        "best": {
            "id": best.id,
            "character": best.character,
            "value": best.metrics[key],
            "result": best.result,
            "score_line": best.score_line,
        }
        if best
        else None,
        "rank": next((m.rank_label for m in counted if m.rank_label), None),
    }


class ReportStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def create(self, session: dict[str, Any], clip_ids: list[int]) -> dict[str, Any]:
        data = {"seconds": session["seconds"], "clips": clip_ids, "stats": None, "stats_game": None}
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO reports (session_id, game_id, game_name, started_at, ended_at, data) VALUES (?, ?, ?, ?, ?, ?)",
                (session.get("id"), session["game_id"], session["game_name"], session["started_at"], session["ended_at"], json.dumps(data)),
            )
            self.db.commit()
        return self.get(cur.lastrowid)

    def attach_stats(self, report_id: int, stats_game: str, stats: dict[str, Any] | None) -> dict[str, Any]:
        report = self.get(report_id)
        data = {k: report[k] for k in ("seconds", "clips")} | {"stats": stats, "stats_game": stats_game}
        with self._lock:
            self.db.execute("UPDATE reports SET data = ? WHERE id = ?", (json.dumps(data), report_id))
            self.db.commit()
        return self.get(report_id)

    def get(self, report_id: int) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute(
                "SELECT id, session_id, game_id, game_name, started_at, ended_at, data FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        if row is None:
            raise KeyError(report_id)
        return self._row(row)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id, session_id, game_id, game_name, started_at, ended_at, data FROM reports ORDER BY ended_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: tuple) -> dict[str, Any]:
        rid, sid, gid, name, start, end, data = row
        return {"id": rid, "session_id": sid, "game_id": gid, "game_name": name, "started_at": start, "ended_at": end, **json.loads(data)}
