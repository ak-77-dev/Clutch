"""Playtime: notice when a library game is running and record play sessions.

A background thread lists running processes every few seconds and matches each
executable against library install folders (longest folder wins, so
``Riot Games/VALORANT/live`` beats ``Riot Games``). Sessions are written as
they happen, with an ``ended_at`` heartbeat, so a crash or reboot never loses
more than one poll of playtime.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from clutch.local.library import Game

MIN_SESSION_S = 60  # shorter blips (launcher splash screens, crashes on boot) aren't sessions

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     TEXT NOT NULL,
    game_name   TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS sessions_game ON sessions(game_id, started_at);
"""

# Library games whose stats Clutch also tracks, for "my stats" links and auto-sync.
STATS_GAMES = {
    "riot:valorant": "valorant",
    "riot:league_of_legends": "lol",
    "epic:Sugar": "rocketleague",
    "steam:252950": "rocketleague",
    "steam:570": "dota2",
    "steam:1422450": "deadlock",
    "bnet:auks": "cod",
    "bnet:odin": "cod",
}


def stats_game_for(game: Game) -> str | None:
    if game.id in STATS_GAMES:
        return STATS_GAMES[game.id]
    return "cod" if "cod" in game.tags else None


@dataclass
class Running:
    game: Game
    session_id: int
    started_at: float


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class ProcessMatcher:
    """Maps running executables to library games by install folder."""

    def __init__(self, games: Iterable[Game]) -> None:
        dirs = [(_norm(g.install_dir), g) for g in games if g.install_dir]
        # Longest prefix first, so nested installs resolve to the most specific game.
        self.dirs = sorted(dirs, key=lambda d: len(d[0]), reverse=True)

    def match(self, exe: str) -> Game | None:
        path = _norm(exe)
        for folder, game in self.dirs:
            if path.startswith(folder + os.sep):
                return game
        return None


def running_executables() -> set[str]:  # pragma: no cover - reads the live process table
    import psutil

    me = os.getpid()
    exes = set()
    for proc in psutil.process_iter(["pid", "exe"]):
        exe = proc.info.get("exe")
        if exe and proc.info["pid"] != me:
            exes.add(exe)
    return exes


class PlaytimeTracker:
    def __init__(
        self,
        db_path: Path | str,
        games: Callable[[], Iterable[Game]],
        *,
        list_processes: Callable[[], set[str]] = running_executables,
        clock: Callable[[], float] = time.time,
        interval: float = 5.0,
    ) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._games = games
        self._list = list_processes
        self._clock = clock
        self.interval = interval
        self._lock = threading.RLock()
        self.running: dict[str, Running] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_start: list[Callable[[Game], None]] = []
        self.on_stop: list[Callable[[Game, dict[str, Any]], None]] = []
        self._close_orphans()

    def _close_orphans(self) -> None:
        """Sessions still marked active are from a previous run that didn't shut down cleanly."""
        with self._lock:
            self.db.execute("UPDATE sessions SET active = 0 WHERE active = 1")
            self.db.execute("DELETE FROM sessions WHERE ended_at - started_at < ?", (MIN_SESSION_S,))
            self.db.commit()

    # ── polling ─────────────────────────────────────────────────────────────
    def poll(self) -> None:
        now = self._clock()
        matcher = ProcessMatcher(self._games())
        seen: dict[str, Game] = {}
        for exe in self._list():
            game = matcher.match(exe)
            if game:
                seen[game.id] = game
        with self._lock:
            for gid, game in seen.items():
                if gid in self.running:
                    self.db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now, self.running[gid].session_id))
                else:
                    cur = self.db.execute(
                        "INSERT INTO sessions (game_id, game_name, started_at, ended_at) VALUES (?, ?, ?, ?)", (gid, game.name, now, now)
                    )
                    self.running[gid] = Running(game, cur.lastrowid, now)
                    self.db.commit()
                    for fn in self.on_start:
                        _safe(fn, game)
            for gid in [g for g in self.running if g not in seen]:
                self._finish(gid, now)
            self.db.commit()

    def _finish(self, gid: str, now: float) -> None:
        run = self.running.pop(gid)
        self.db.execute("UPDATE sessions SET ended_at = ?, active = 0 WHERE id = ?", (now, run.session_id))
        duration = now - run.started_at
        if duration < MIN_SESSION_S:
            self.db.execute("DELETE FROM sessions WHERE id = ?", (run.session_id,))
            self.db.commit()
            return
        self.db.commit()
        info = {
            "id": run.session_id,
            "game_id": gid,
            "game_name": run.game.name,
            "started_at": run.started_at,
            "ended_at": now,
            "seconds": duration,
        }
        for fn in self.on_stop:
            _safe(fn, run.game, info)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clutch-playtime", daemon=True)
        self._thread.start()

    def _loop(self) -> None:  # pragma: no cover - timing loop around poll()
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                self.poll()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            now = self._clock()
            for gid in list(self.running):
                self._finish(gid, now)

    # ── queries ─────────────────────────────────────────────────────────────
    def now_playing(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [
            {"game_id": r.game.id, "game_name": r.game.name, "started_at": r.started_at, "seconds": now - r.started_at}
            for r in self.running.values()
        ]

    def summary(self) -> list[dict[str, Any]]:
        """Per game: total seconds, sessions, last played, and the last 7 / 30 days."""
        now = self._clock()
        with self._lock:
            rows = self.db.execute(
                """
                SELECT game_id, MAX(game_name), SUM(ended_at - started_at), COUNT(*), MAX(ended_at),
                       SUM(CASE WHEN started_at >= ? THEN ended_at - started_at ELSE 0 END),
                       SUM(CASE WHEN started_at >= ? THEN ended_at - started_at ELSE 0 END)
                FROM sessions GROUP BY game_id ORDER BY MAX(ended_at) DESC
                """,
                (now - 7 * 86400, now - 30 * 86400),
            ).fetchall()
        return [
            {
                "game_id": r[0],
                "game_name": r[1],
                "seconds": r[2] or 0,
                "sessions": r[3],
                "last_played": r[4],
                "week": r[5] or 0,
                "month": r[6] or 0,
            }
            for r in rows
        ]

    def sessions(self, game_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT id, game_id, game_name, started_at, ended_at, active FROM sessions"
        args: tuple = ()
        if game_id:
            sql += " WHERE game_id = ?"
            args = (game_id,)
        sql += " ORDER BY started_at DESC LIMIT ?"
        with self._lock:
            rows = self.db.execute(sql, (*args, limit)).fetchall()
        return [
            {
                "id": r[0],
                "game_id": r[1],
                "game_name": r[2],
                "started_at": r[3],
                "ended_at": r[4],
                "seconds": r[4] - r[3],
                "active": bool(r[5]),
            }
            for r in rows
        ]

    def daily(self, days: int = 91, tz: timezone | None = None) -> list[dict[str, Any]]:
        """Seconds played per local calendar day (sessions crossing midnight are split)."""
        tz = tz or datetime.now().astimezone().tzinfo
        end_day = datetime.fromtimestamp(self._clock(), tz).date()
        start_day = end_day - timedelta(days=days - 1)
        totals = {start_day + timedelta(days=i): 0.0 for i in range(days)}
        per_game: dict[Any, dict[str, float]] = {d: {} for d in totals}
        since = datetime.combine(start_day, datetime.min.time(), tz).timestamp()
        with self._lock:
            rows = self.db.execute("SELECT game_id, started_at, ended_at FROM sessions WHERE ended_at >= ?", (since,)).fetchall()
        for gid, a, b in rows:
            t = max(a, since)
            while t < b:
                day = datetime.fromtimestamp(t, tz).date()
                midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz).timestamp()
                chunk = min(b, midnight) - t
                if day in totals:
                    totals[day] += chunk
                    per_game[day][gid] = per_game[day].get(gid, 0) + chunk
                t = midnight
        return [
            {"date": d.isoformat(), "seconds": round(totals[d]), "games": {k: round(v) for k, v in per_game[d].items()}}
            for d in sorted(totals)
        ]


def _safe(fn: Callable, *args: Any) -> None:
    with contextlib.suppress(Exception):  # a listener must never kill the monitor thread
        fn(*args)
