"""Wires the desktop features together and reacts to what the player is doing.

- A game starts   -> (optionally) start the replay buffer; clips are named after it
- A game closes   -> session recap event; auto-refresh the player's linked stats
- Hotkeys (sent by the Electron shell) -> save clip / toggle recording / screenshot

Everything user-visible is also published on an in-process event bus that the
UI and the Electron shell subscribe to (Server-Sent Events).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from clutch.local.art import ArtResolver
from clutch.local.capture import CaptureConfig, ReplayBuffer, screenshot
from clutch.local.clips import ClipStore
from clutch.local.config import Settings, SettingsStore, data_dir
from clutch.local.library import Game, Library
from clutch.local.playtime import PlaytimeTracker, stats_game_for


class EventBus:
    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.recent: list[dict[str, Any]] = []

    def publish(self, type_: str, **data: Any) -> dict[str, Any]:
        event = {"type": type_, "at": time.time(), **data}
        with self._lock:
            self.recent = [*self.recent[-49:], event]
            for q in self._subs:
                q.put(event)
        return event

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @staticmethod
    def sse(event: dict[str, Any]) -> str:
        return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


class Desktop:
    def __init__(self, stats_service: Any = None, *, home: Path | None = None, start_threads: bool = True) -> None:
        self.home = home or data_dir()
        self.stats = stats_service
        self.events = EventBus()
        self.settings = SettingsStore(self.home / "settings.json")
        self.library = Library(self.home / "library.json")
        self.art = ArtResolver(self.home / "art")
        self.clips = ClipStore(self.home / "local.db", self.home / "thumbs")
        self.buffer = ReplayBuffer(self.home / "spool")
        # Hidden entries (tools like Wallpaper Engine, anything the player hid) aren't tracked as games.
        self.playtime = PlaytimeTracker(self.home / "local.db", lambda: self.library.all())
        self._auto_buffer = False  # started by a game launch (so stop it when games close)
        self._lock = threading.RLock()
        self.playtime.on_start.append(self._game_started)
        self.playtime.on_stop.append(self._game_stopped)
        self.settings.on_change(self._settings_changed)
        self.clips_root.mkdir(parents=True, exist_ok=True)
        if start_threads:
            self.buffer.cleanup_stale()
            self.library.scan()  # ~20 ms; picks up games installed since last run
            threading.Thread(target=self._warm_art, name="clutch-art", daemon=True).start()
            self.playtime.start()

    # ── helpers ─────────────────────────────────────────────────────────────
    @property
    def cfg(self) -> Settings:
        return self.settings.get()

    @property
    def clips_root(self) -> Path:
        return Path(self.cfg.clips_dir)

    def capture_config(self) -> CaptureConfig:
        s = self.cfg
        return CaptureConfig(
            fps=s.fps,
            quality=s.quality,
            encoder=s.encoder,
            monitor=s.monitor,
            buffer_seconds=s.buffer_seconds,
            system_audio=s.record_system_audio,
            mic=s.record_mic,
        )

    def current_game(self) -> Game | None:
        running = sorted(self.playtime.running.values(), key=lambda r: r.started_at, reverse=True)
        return running[0].game if running else None

    def _warm_art(self) -> None:
        for g in self.library.all(include_hidden=True):
            try:
                self.art.describe(g)
            except Exception:
                continue

    # ── game lifecycle ──────────────────────────────────────────────────────
    def _game_started(self, game: Game) -> None:
        self.events.publish("game_started", game_id=game.id, game_name=game.name, stats_game=stats_game_for(game))
        if self.cfg.auto_buffer and not self.buffer.active:
            try:
                self.buffer.start(self.capture_config())
                self._auto_buffer = True
                self.events.publish("buffer", **self.buffer.status(), reason=f"{game.name} started")
            except Exception as exc:
                self.events.publish("error", message=f"Couldn't start the replay buffer: {exc}")

    def _game_stopped(self, game: Game, session: dict[str, Any]) -> None:
        clips = [c for c in self.clips.list(game_id=game.id) if c["created_at"] >= session["started_at"] - 5]
        self.events.publish("session_end", **session, clips=len(clips), notify=self.cfg.notify_sessions)
        if self._auto_buffer and not self.playtime.running and not self.buffer.recording_since:
            self.buffer.stop()
            self._auto_buffer = False
            self.events.publish("buffer", **self.buffer.status(), reason="no games running")
        stats_game = stats_game_for(game)
        key = self.cfg.linked_profiles.get(stats_game or "")
        if self.cfg.auto_sync_on_exit and self.stats and stats_game and key:
            threading.Thread(target=self._auto_sync, args=(stats_game, key), daemon=True).start()

    def _auto_sync(self, game: str, key: str) -> None:
        time.sleep(20)  # match history APIs lag a little behind the end of a game
        try:
            result = self.stats.sync(game, key)
            self.events.publish("stats_synced", game=game, key=key, new=result.get("new", 0))
        except Exception as exc:
            self.events.publish("error", message=f"Couldn't refresh {game} stats: {exc}")

    def _settings_changed(self, s: Settings) -> None:
        if self.buffer.active and not self.buffer.recording_since:
            new = self.capture_config()
            if asdict(new) != asdict(self.buffer.cfg):
                self.buffer.stop()  # restart so fps / quality / audio changes apply
                self.buffer.start(new)
                self.events.publish("buffer", **self.buffer.status(), reason="settings changed")

    # ── capture actions ─────────────────────────────────────────────────────
    def set_buffer(self, on: bool) -> dict[str, Any]:
        if on and not self.buffer.active:
            self.buffer.start(self.capture_config())
            self._auto_buffer = False
        elif not on and self.buffer.active:
            self.buffer.stop()
            self._auto_buffer = False
        status = self.buffer.status()
        self.events.publish("buffer", **status)
        return status

    def save_clip(self, seconds: float | None = None) -> dict[str, Any]:
        if not self.buffer.active:
            self.set_buffer(True)
            raise RuntimeError("The replay buffer was off, so it's on now. Press the hotkey again in a few seconds.")
        game = self.current_game()
        dest = ClipStore.new_path(self.clips_root, game.name if game else None, "clip")
        self.buffer.save(dest, seconds=seconds or self.cfg.buffer_seconds)
        clip = self.clips.add(dest, kind="clip", game_id=game.id if game else None, game_name=game.name if game else None)
        self.events.publish("clip_saved", clip=clip)
        return clip

    def toggle_recording(self) -> dict[str, Any]:
        if self.buffer.recording_since is None:
            if not self.buffer.active:
                self.buffer.start(self.capture_config())
            self.buffer.start_recording()
            status = self.buffer.status()
            self.events.publish("recording", **status)
            return {"recording": True, "status": status}
        game = self.current_game()
        dest = ClipStore.new_path(self.clips_root, game.name if game else None, "recording")
        self.buffer.stop_recording(dest)
        clip = self.clips.add(dest, kind="recording", game_id=game.id if game else None, game_name=game.name if game else None)
        self.events.publish("recording", **self.buffer.status())
        self.events.publish("clip_saved", clip=clip)
        return {"recording": False, "clip": clip}

    def take_screenshot(self) -> dict[str, Any]:
        game = self.current_game()
        dest = ClipStore.new_path(self.clips_root, game.name if game else None, "screenshot", ".png")
        screenshot(dest, self.cfg.monitor)
        shot = self.clips.add(dest, kind="screenshot", game_id=game.id if game else None, game_name=game.name if game else None)
        self.events.publish("clip_saved", clip=shot)
        return shot

    # ── read models ─────────────────────────────────────────────────────────
    def library_view(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        totals = {s["game_id"]: s for s in self.playtime.summary()}
        running = {r["game_id"] for r in self.playtime.now_playing()}
        clip_counts: dict[str, int] = {}
        for c in self.clips.list():
            if c["game_id"]:
                clip_counts[c["game_id"]] = clip_counts.get(c["game_id"], 0) + 1
        out = []
        for g in self.library.all(include_hidden=include_hidden):
            t = totals.get(g.id, {})
            out.append(
                {
                    **g.to_dict(),
                    "art": self.art.describe(g, resolve=False),
                    "playtime": t.get("seconds", 0),
                    "week": t.get("week", 0),
                    "last_played": max(filter(None, [t.get("last_played"), g.last_played]), default=None),
                    "running": g.id in running,
                    "clips": clip_counts.get(g.id, 0),
                    "hidden": g.id in self.library.hidden,
                    "stats_game": stats_game_for(g),
                }
            )
        return out

    def playtime_report(self, days: int) -> dict[str, Any]:
        """Playtime for the UI, leaving out entries the player hid (tools, uninstalled games)."""
        hidden = self.library.hidden
        daily = self.playtime.daily(days)
        for d in daily:
            for gid in [g for g in d["games"] if g in hidden]:
                d["seconds"] -= d["games"].pop(gid)
        return {
            "now": [n for n in self.playtime.now_playing() if n["game_id"] not in hidden],
            "games": [g for g in self.playtime.summary() if g["game_id"] not in hidden],
            "daily": daily,
            "sessions": [s for s in self.playtime.sessions(limit=60) if s["game_id"] not in hidden][:40],
        }

    def status(self) -> dict[str, Any]:
        return {
            "buffer": self.buffer.status(),
            "now_playing": self.playtime.now_playing(),
            "clips": self.clips.stats(),
            "clips_dir": str(self.clips_root),
        }

    def shutdown(self) -> None:
        self.playtime.stop()
        self.buffer.stop()
