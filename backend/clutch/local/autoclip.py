"""Auto-clipping: save highlights on their own, from the events games report.

Only games with an official live event feed can do this; everything else still
has the clip hotkey.

- League of Legends: Riot's Live Client Data API (``https://127.0.0.1:2999``,
  in game only) lists ChampionKill / Multikill / Ace events.
- Dota 2 and Counter-Strike 2: Valve's Game State Integration. The game POSTs
  its state to a URL named in a ``gamestate_integration_*.cfg`` file; Clutch
  listens on a fixed local port (the app's own port changes every launch) and
  checks a secret token the config carries.
- Valorant has no such feed (Riot offers none for it), so it isn't here.

Detected highlights are debounced: a clip is saved a few seconds after the
last kill, so a triple kill makes one clip, not three.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

GSI_PORT = 47299
LEVELS = {"kill": 1, "multikill": 2, "ace": 3}
POST_ROLL_S = 4.0  # keep recording this long after the last kill so the clip has the aftermath
DOTA_MULTIKILL_WINDOW_S = 18.0
MULTI_NAMES = {2: "Double kill", 3: "Triple kill", 4: "Quadra kill", 5: "Penta kill"}
DOTA_NAMES = {2: "Double kill", 3: "Triple kill", 4: "Ultra kill", 5: "Rampage"}
CS_NAMES = {1: "Kill", 2: "Double kill", 3: "3K", 4: "4K", 5: "Ace"}


@dataclass
class Highlight:
    game: str  # library game id
    level: str  # kill | multikill | ace
    title: str
    at: float = field(default_factory=time.time)


# ── debouncing clipper ───────────────────────────────────────────────────────


class AutoClipper:
    """Collects highlights and saves one clip per burst of action."""

    def __init__(self, save: Callable[[str, float], Any], min_level: Callable[[], str], clip_seconds: Callable[[], float]) -> None:
        self._save = save
        self._min_level = min_level
        self._clip_seconds = clip_seconds
        self._pending: Highlight | None = None
        self._burst_start = 0.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self.saved: list[str] = []

    def offer(self, h: Highlight) -> bool:
        if LEVELS[h.level] < LEVELS.get(self._min_level(), 2):
            return False
        with self._lock:
            if self._pending is None:
                self._burst_start = h.at
                self._pending = h
            elif LEVELS[h.level] >= LEVELS[self._pending.level]:
                self._pending = h  # the biggest moment names the clip
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(POST_ROLL_S, self._fire)
            self._timer.daemon = True
            self._timer.start()
        return True

    def _fire(self) -> None:
        with self._lock:
            h, self._pending, self._timer = self._pending, None, None
            burst = time.time() - self._burst_start
        if h is None:
            return
        # Long enough for the whole burst plus some lead-in, never beyond the buffer.
        seconds = min(self._clip_seconds(), max(20.0, burst + 12))
        try:
            self._save(h.title, seconds)
            self.saved.append(h.title)
        except Exception:
            pass  # buffer off or disk trouble: the desktop layer reports errors itself

    def flush(self) -> None:
        """Save a pending highlight now (tests, and when the game closes)."""
        with self._lock:
            timer = self._timer
        if timer:
            timer.cancel()
            self._fire()


# ── League of Legends (Live Client Data API) ─────────────────────────────────


def league_highlights(events: list[dict[str, Any]], me: str, seen: set[int]) -> list[Highlight]:
    """New highlights for the active player from a Live Client Data event list."""
    me_name = me.split("#", 1)[0].casefold()
    out = []
    for e in events:
        eid = e.get("EventID")
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        name = e.get("EventName")
        killer = str(e.get("KillerName") or e.get("Acer") or "").split("#", 1)[0].casefold()
        if killer != me_name:
            continue
        if name == "ChampionKill":
            out.append(Highlight("riot:league_of_legends", "kill", "Kill"))
        elif name == "Multikill":
            streak = int(e.get("KillStreak") or 2)
            out.append(
                Highlight("riot:league_of_legends", "ace" if streak >= 4 else "multikill", MULTI_NAMES.get(streak, f"{streak}x kill"))
            )
        elif name == "Ace":
            out.append(Highlight("riot:league_of_legends", "ace", "Ace"))
    return out


class LeaguePoller:
    URL = "https://127.0.0.1:2999/liveclientdata"

    def __init__(self, on_highlight: Callable[[Highlight], Any], is_running: Callable[[], bool]) -> None:
        self.on_highlight = on_highlight
        self.is_running = is_running
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="clutch-lol-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:  # pragma: no cover - needs a live League game
        import requests
        import urllib3

        urllib3.disable_warnings()  # the game serves a self-signed certificate on localhost
        seen: set[int] = set()
        me = None
        while not self._stop.wait(1.5):
            if not self.is_running():
                seen.clear()
                me = None
                continue
            try:
                if me is None:
                    me = requests.get(f"{self.URL}/activeplayername", verify=False, timeout=2).json()
                    first = requests.get(f"{self.URL}/eventdata", verify=False, timeout=2).json().get("Events", [])
                    seen.update(e.get("EventID") for e in first)  # don't clip what happened before Clutch was watching
                    continue
                events = requests.get(f"{self.URL}/eventdata", verify=False, timeout=2).json().get("Events", [])
                for h in league_highlights(events, me, seen):
                    self.on_highlight(h)
            except Exception:
                me = None  # loading screen, or the game ended


# ── Valve Game State Integration (Dota 2, CS2) ───────────────────────────────


class DotaTracker:
    """Turns successive Dota 2 GSI payloads into kill / multikill highlights."""

    def __init__(self) -> None:
        self.kills: int | None = None
        self.match: str | None = None
        self.recent: list[float] = []

    def update(self, payload: dict[str, Any], now: float | None = None) -> list[Highlight]:
        now = now or time.time()
        player = payload.get("player") or {}
        match = (payload.get("map") or {}).get("matchid")
        kills = player.get("kills")
        if kills is None:
            return []
        if match != self.match:  # a new match: start counting from here
            self.match, self.kills, self.recent = match, kills, []
            return []
        gained = kills - (self.kills or 0)
        self.kills = kills
        if gained <= 0:
            return []
        self.recent = [t for t in self.recent if now - t <= DOTA_MULTIKILL_WINDOW_S] + [now] * gained
        n = len(self.recent)
        if n >= 2:
            return [Highlight("steam:570", "ace" if n >= 4 else "multikill", DOTA_NAMES.get(min(n, 5), "Rampage"))]
        return [Highlight("steam:570", "kill", "Kill")]


class CsTracker:
    """Counter-Strike 2: round kills of the local player (not whoever you're spectating)."""

    def __init__(self) -> None:
        self.round_kills = 0
        self.round: Any = None

    def update(self, payload: dict[str, Any]) -> list[Highlight]:
        me = (payload.get("provider") or {}).get("steamid")
        player = payload.get("player") or {}
        if not me or player.get("steamid") != me:
            return []
        rnd = (payload.get("map") or {}).get("round")
        kills = int((player.get("state") or {}).get("round_kills") or 0)
        if rnd != self.round:
            self.round, self.round_kills = rnd, 0
        if kills <= self.round_kills:
            self.round_kills = kills
            return []
        self.round_kills = kills
        level = "ace" if kills >= 4 else ("multikill" if kills >= 2 else "kill")
        return [Highlight("steam:730", level, CS_NAMES.get(kills, f"{kills}K"))]


def gsi_token(home: Path) -> str:
    path = home / "gsi_token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        token = secrets.token_urlsafe(24)
        path.write_text(token, encoding="utf-8")
        return token


GSI_TARGETS = {
    # library id -> (cfg folder under the install dir, the file Valve wants, which data to ask for)
    "steam:570": ("game/dota/cfg/gamestate_integration", "gamestate_integration_clutch.cfg", ["provider", "map", "player", "hero"]),
    "steam:730": (
        "game/csgo/cfg",
        "gamestate_integration_clutch.cfg",
        ["provider", "map", "round", "player_id", "player_state", "player_match_stats"],
    ),
}


def gsi_config(game_id: str, token: str) -> str:
    _folder, _name, data = GSI_TARGETS[game_id]
    path = "dota" if game_id == "steam:570" else "cs2"
    lines = [
        '"Clutch"',
        "{",
        f'    "uri" "http://127.0.0.1:{GSI_PORT}/gsi/{path}"',
        '    "timeout" "5.0"',
        '    "buffer" "0.1"',
        '    "throttle" "0.1"',
        '    "heartbeat" "30.0"',
        f'    "auth" {{ "token" "{token}" }}',
        '    "data"',
        "    {",
        *[f'        "{d}" "1"' for d in data],
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def install_gsi(game_id: str, install_dir: str, token: str) -> Path:
    if game_id not in GSI_TARGETS:
        raise ValueError("This game has no Game State Integration")
    folder, name, _ = GSI_TARGETS[game_id]
    target = Path(install_dir) / folder
    if not target.parent.exists():
        raise ValueError(f"Couldn't find the game's config folder in {install_dir}")
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_text(gsi_config(game_id, token), encoding="utf-8")
    return path


def gsi_installed(game_id: str, install_dir: str) -> bool:
    if game_id not in GSI_TARGETS:
        return False
    folder, name, _ = GSI_TARGETS[game_id]
    return (Path(install_dir) / folder / name).exists()


class GsiServer:
    """Receives Dota 2 / CS2 state on 127.0.0.1:47299 and turns it into highlights."""

    def __init__(self, token: str, on_highlight: Callable[[Highlight], Any], port: int = GSI_PORT) -> None:
        self.token = token
        self.on_highlight = on_highlight
        self.port = port
        self.trackers = {"dota": DotaTracker(), "cs2": CsTracker()}
        self.httpd: ThreadingHTTPServer | None = None

    def handle(self, game: str, payload: dict[str, Any]) -> list[Highlight]:
        if (payload.get("auth") or {}).get("token") != self.token:
            return []
        tracker = self.trackers.get(game)
        highlights = tracker.update(payload) if tracker else []
        for h in highlights:
            self.on_highlight(h)
        return highlights

    def start(self) -> bool:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                game = self.path.rstrip("/").rsplit("/", 1)[-1]
                try:
                    body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 2_000_000))
                    server.handle(game, json.loads(body or b"{}"))
                except (ValueError, OSError):
                    pass
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args: Any) -> None:
                return

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            return False  # port taken (another Clutch instance?): auto-clip for GSI games is off
        threading.Thread(target=self.httpd.serve_forever, name="clutch-gsi", daemon=True).start()
        return True

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
