"""Goals: targets the player sets, with progress computed from what Clutch already tracks.

- ``daily_cap``   play at most N hours a day (one game or all games)
- ``weekly_hours`` play at least N hours this week (practice goals)
- ``clips``       save N clips this week
- ``win_rate``    win at least N% of your last 20 games (linked stats account)
- ``rank``        reach a rank on the game's ladder (linked stats account)
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

KINDS = ("daily_cap", "weekly_hours", "clips", "win_rate", "rank")

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    game_id     TEXT,             -- library game (time goals) or stats game (win_rate / rank)
    target      REAL NOT NULL,
    label       TEXT,             -- e.g. the rank name for rank goals
    created_at  REAL NOT NULL,
    done_at     REAL,
    notified    TEXT              -- last state we told the player about
);
"""


def week_start(now: float) -> float:
    d = datetime.fromtimestamp(now).astimezone()
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def day_start(now: float) -> float:
    return datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class GoalStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add(self, kind: str, target: float, game_id: str | None = None, label: str | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if target <= 0:
            raise ValueError("target must be positive")
        if kind in ("win_rate", "rank") and not game_id:
            raise ValueError("pick the game this goal is for")
        if kind == "win_rate" and target > 100:
            raise ValueError("a win rate can't be over 100%")
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO goals (kind, game_id, target, label, created_at) VALUES (?, ?, ?, ?, ?)",
                (kind, game_id, target, label, time.time()),
            )
            self.db.commit()
        return self.get(cur.lastrowid)

    def get(self, goal_id: int) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute(
                "SELECT id, kind, game_id, target, label, created_at, done_at, notified FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        return dict(zip(("id", "kind", "game_id", "target", "label", "created_at", "done_at", "notified"), row, strict=True))

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM goals ORDER BY created_at").fetchall()]
        return [self.get(i) for i in ids]

    def delete(self, goal_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            self.db.commit()

    def mark(self, goal_id: int, *, notified: str | None = None, done: bool | None = None) -> None:
        with self._lock:
            if notified is not None:
                self.db.execute("UPDATE goals SET notified = ? WHERE id = ?", (notified, goal_id))
            if done is not None:
                self.db.execute("UPDATE goals SET done_at = ? WHERE id = ?", (time.time() if done else None, goal_id))
            self.db.commit()


def evaluate(goal: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Progress for one goal. ``ctx`` holds what the Desktop knows right now:

    - ``daily``: {"date", "seconds", "games": {game_id: s}} for the last few days (today last)
    - ``clips``: clip rows
    - ``profiles``: stats game -> {"rank_value", "rank_label", "recent": ["win"|"loss"...]}
    - ``now``
    """
    kind, target, game = goal["kind"], goal["target"], goal["game_id"]
    now = ctx.get("now", time.time())

    def seconds_since(since: float) -> float:
        total = 0.0
        for d in ctx.get("daily", []):
            day_ts = datetime.fromisoformat(d["date"]).astimezone().timestamp()
            if day_ts >= since - 1:
                total += d["games"].get(game, 0) if game else d["seconds"]
        return total

    if kind == "daily_cap":
        value = seconds_since(day_start(now)) / 3600
        state = "over" if value > target else ("close" if value > target * 0.8 else "ok")
        return {"value": round(value, 2), "target": target, "unit": "h today", "progress": min(1, value / target), "state": state}
    if kind == "weekly_hours":
        value = seconds_since(week_start(now)) / 3600
        return {
            "value": round(value, 2),
            "target": target,
            "unit": "h this week",
            "progress": min(1, value / target),
            "state": "done" if value >= target else "ok",
        }
    if kind == "clips":
        since = week_start(now)
        value = sum(1 for c in ctx.get("clips", []) if c["created_at"] >= since and (not game or c["game_id"] == game))
        return {
            "value": value,
            "target": target,
            "unit": "clips this week",
            "progress": min(1, value / target),
            "state": "done" if value >= target else "ok",
        }
    profile = ctx.get("profiles", {}).get(game)
    if profile is None:
        return {"value": None, "target": target, "unit": "", "progress": 0, "state": "unlinked"}
    if kind == "win_rate":
        recent = [r for r in profile.get("recent", []) if r in ("win", "loss")][:20]
        value = 100 * sum(r == "win" for r in recent) / len(recent) if recent else 0
        return {
            "value": round(value, 1),
            "target": target,
            "unit": f"% over {len(recent)} games",
            "progress": min(1, value / target),
            "state": "done" if value >= target and len(recent) >= 10 else "ok",
        }
    value = profile.get("rank_value")  # rank
    if value is None:
        return {"value": None, "target": target, "unit": "", "progress": 0, "state": "unranked", "current": profile.get("rank_label")}
    return {
        "value": value,
        "target": target,
        "unit": "",
        "progress": min(1, value / target) if target else 1,
        "state": "done" if value >= target else "ok",
        "current": profile.get("rank_label"),
    }
