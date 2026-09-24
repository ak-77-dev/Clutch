"""Wires the desktop features together and reacts to what the player is doing.

- A game starts   -> (optionally) start the replay buffer; clips are named after it
- In game         -> auto-clip highlights (League / Dota 2 / CS2 event feeds)
- A game closes   -> session report card; auto-refresh the player's linked stats
- Hotkeys (sent by the Electron shell) -> save clip / toggle recording / screenshot
- Every minute    -> goal checks, friends' profiles refresh, storage limits

Everything user-visible is also published on an in-process event bus that the
UI and the Electron shell subscribe to (Server-Sent Events).
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from clutch.local import edit, share
from clutch.local.art import ArtResolver
from clutch.local.autoclip import AutoClipper, GsiServer, Highlight, LeaguePoller, gsi_installed, gsi_token, install_gsi
from clutch.local.capture import CaptureConfig, ReplayBuffer, screenshot
from clutch.local.clips import ClipStore
from clutch.local.config import Settings, SettingsStore, data_dir
from clutch.local.friends import FriendStore
from clutch.local.friends import feed as friends_feed
from clutch.local.goals import GoalStore, evaluate
from clutch.local.library import Game, Library
from clutch.local.playtime import PlaytimeTracker, stats_game_for
from clutch.local.reports import ReportStore, iso_ts, match_window, summarize_matches
from clutch.local.storage import plan_cleanup


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
        self.art = ArtResolver(self.home / "art", sgdb_key=lambda: self.cfg.steamgriddb_key)
        self.clips = ClipStore(self.home / "local.db", self.home / "thumbs")
        self.buffer = ReplayBuffer(self.home / "spool")
        # Hidden entries (tools like Wallpaper Engine, anything the player hid) aren't tracked as games.
        self.playtime = PlaytimeTracker(self.home / "local.db", lambda: self.library.all())
        self.reports = ReportStore(self.home / "local.db")
        self.goals = GoalStore(self.home / "local.db")
        self.friends = FriendStore(self.home / "friends.json")
        self.autoclip = AutoClipper(
            save=lambda title, seconds: self.save_clip(seconds, title=title, auto=True),
            min_level=lambda: self.cfg.auto_clip_min,
            clip_seconds=lambda: float(self.cfg.buffer_seconds),
        )
        self.gsi = GsiServer(gsi_token(self.home), self._highlight)
        self.league = LeaguePoller(self._highlight, lambda: "riot:league_of_legends" in self.playtime.running)
        self._auto_buffer = False  # started by a game launch (so stop it when games close)
        self._stop = threading.Event()
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
            self.gsi.start()
            self.league.start()
            threading.Thread(target=self._minutely, name="clutch-minutely", daemon=True).start()

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
            codec=s.codec,
            preset=s.preset,
            rate_control=s.rate_control,
            bitrate_mbps=s.bitrate_mbps,
            resolution=s.resolution,
            monitor=s.monitor,
            buffer_seconds=s.buffer_seconds,
            system_audio=s.record_system_audio,
            mic=s.record_mic,
            audio_device=s.audio_device,
            mic_device=s.mic_device,
            audio_kbps=s.audio_kbps,
            separate_tracks=s.separate_audio_tracks,
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
        self.autoclip.flush()  # a highlight right before quitting still gets saved
        clips = [c for c in self.clips.list(game_id=game.id) if c["created_at"] >= session["started_at"] - 5]
        self.events.publish("session_end", **session, clips=len(clips), notify=self.cfg.notify_sessions)
        report = self.reports.create(session, [c["id"] for c in clips])
        if self._auto_buffer and not self.playtime.running and not self.buffer.recording_since:
            self.buffer.stop()
            self._auto_buffer = False
            self.events.publish("buffer", **self.buffer.status(), reason="no games running")
        stats_game = stats_game_for(game)
        key = self.cfg.linked_profiles.get(stats_game or "")
        if self.cfg.auto_sync_on_exit and self.stats and stats_game and key:
            threading.Thread(target=self._auto_sync, args=(stats_game, key, report["id"]), daemon=True).start()
        else:
            self.events.publish("report_ready", report=report)

    def _auto_sync(self, game: str, key: str, report_id: int | None = None, delay: float = 20) -> None:
        time.sleep(delay)  # match history APIs lag a little behind the end of a game
        try:
            result = self.stats.sync(game, key)
            self.events.publish("stats_synced", game=game, key=key, new=result.get("new", 0))
        except Exception as exc:
            self.events.publish("error", message=f"Couldn't refresh {game} stats: {exc}")
        if report_id is not None:
            self.events.publish("report_ready", report=self.fill_report(report_id, game, key))

    def fill_report(self, report_id: int, game: str, key: str) -> dict[str, Any]:
        """Add the session's matches (W/L, best game, form vs usual) to a report card."""
        report = self.reports.get(report_id)
        try:
            matches = self.stats.matches(game, key)
            meta = self.stats.provider(game).meta
        except Exception:
            return report
        played = match_window(matches, report["started_at"], report["ended_at"])
        baseline = [m for m in matches if m not in played][:30]
        return self.reports.attach_stats(report_id, game, summarize_matches(played, meta, baseline))

    def _highlight(self, h: Highlight) -> None:
        if not self.cfg.auto_clip or not self.buffer.active:
            return
        if self.autoclip.offer(h):
            self.events.publish("highlight", game_id=h.game, level=h.level, title=h.title)

    # ── background upkeep ───────────────────────────────────────────────────
    def _minutely(self) -> None:  # pragma: no cover - timing loop
        while not self._stop.wait(60):
            for job in (self.check_goals, self.refresh_friends, self.enforce_storage):
                with contextlib.suppress(Exception):
                    job()

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

    # ── API keys ────────────────────────────────────────────────────────────
    def keys_view(self) -> dict[str, Any]:
        from clutch.local import keys

        return keys.status()

    def set_keys(self, values: dict[str, Any]) -> dict[str, Any]:
        from clutch.local import keys

        result = keys.update(values)
        if self.stats is not None:  # providers read their key at construction
            from clutch.games import default_providers

            self.stats.providers = {p.meta.id: p for p in default_providers()}
        return result

    def capabilities(self) -> dict[str, Any]:
        """What this PC can record with: encoders per codec, and audio devices."""
        from clutch.local.capture import capabilities, list_audio_devices

        return {"encoders": capabilities(), "audio": list_audio_devices()}

    def save_clip(self, seconds: float | None = None, *, title: str | None = None, auto: bool = False) -> dict[str, Any]:
        if not self.buffer.active:
            self.set_buffer(True)
            raise RuntimeError("The replay buffer was off, so it's on now. Press the hotkey again in a few seconds.")
        game = self.current_game()
        self.events.publish("clip_saving", game_name=game.name if game else None)
        dest = ClipStore.new_path(self.clips_root, game.name if game else None, "clip")
        started = time.perf_counter()
        info = self.buffer.save(dest, seconds=seconds or self.cfg.buffer_seconds)
        saved = time.perf_counter()
        if title and game:
            title = f"{title} · {game.name}"
        clip = self._register(dest, "clip", game, info, title=f"{'⚡ ' if auto else ''}{title}" if title else None)
        print(
            f"[clip] {info.get('duration', 0):.1f}s saved in {(saved - started) * 1000:.0f} ms"
            f" (prepare {info.get('prepare_ms')} ms, mux {info.get('mux_ms')} ms)"
            f" + indexed in {(time.perf_counter() - saved) * 1000:.0f} ms (audio: {info.get('audio')})",
            flush=True,
        )
        return clip

    def _register(
        self, dest: Path, kind: str, game: Game | None, info: dict[str, Any] | None = None, title: str | None = None
    ) -> dict[str, Any]:
        """Index a new file and announce it right away; the thumbnail follows a moment later."""
        clip = self.clips.add(
            dest,
            kind=kind,
            game_id=game.id if game else None,
            game_name=game.name if game else None,
            info=info,
            thumbnail=False,
            title=title,
        )
        self.events.publish("clip_saved", clip=clip)

        def thumb() -> None:
            with contextlib.suppress(Exception):  # a missing thumbnail must never lose the clip
                self.events.publish("clip_updated", clip=self.clips.make_thumbnail(clip["id"]))

        threading.Thread(target=thumb, name="clutch-thumb", daemon=True).start()
        threading.Thread(target=self.enforce_storage, name="clutch-storage", daemon=True).start()
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
        info = self.buffer.stop_recording(dest)
        self.events.publish("recording", **self.buffer.status())
        return {"recording": False, "clip": self._register(dest, "recording", game, info)}

    def take_screenshot(self) -> dict[str, Any]:
        game = self.current_game()
        dest = ClipStore.new_path(self.clips_root, game.name if game else None, "screenshot", ".png")
        screenshot(dest, self.cfg.monitor)
        return self._register(dest, "screenshot", game)

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

    # ── storage ─────────────────────────────────────────────────────────────
    def storage_plan(self, max_days: int | None = None, max_gb: float | None = None) -> dict[str, Any]:
        plan = plan_cleanup(
            self.clips.list(),
            max_days=self.cfg.storage_max_days if max_days is None else max_days,
            max_gb=self.cfg.storage_max_gb if max_gb is None else max_gb,
        )
        return plan.to_dict()

    def enforce_storage(self) -> dict[str, Any] | None:
        if not (self.cfg.storage_max_days or self.cfg.storage_max_gb):
            return None
        plan = self.storage_plan()
        for clip_id in plan["clip_ids"]:
            with contextlib.suppress(Exception):
                self.clips.delete(clip_id)
        if plan["count"]:
            self.events.publish("storage_cleaned", count=plan["count"], bytes=plan["bytes"])
        return plan

    # ── clips <-> matches ───────────────────────────────────────────────────
    def _stats_game_of(self, library_id: str | None) -> str | None:
        try:
            return stats_game_for(self.library.get(library_id)) if library_id else None
        except KeyError:
            return None

    def clip_match(self, clip_id: int) -> dict[str, Any] | None:
        """The match a clip was recorded in (needs the game's stats account linked)."""
        clip = self.clips.get(clip_id)
        game = self._stats_game_of(clip["game_id"])
        key = self.cfg.linked_profiles.get(game or "")
        if not (self.stats and game and key):
            return None
        end = clip["created_at"]
        start = end - (clip["duration"] or 0)
        for m in self.stats.matches(game, key):
            m_start = iso_ts(m.date)
            if m_start - 30 <= end and start <= m_start + m.duration_s + 30:
                return {"game": game, "key": key, **m.summary_dict()}
        return None

    def match_clips(self, game: str, key: str) -> dict[str, list[int]]:
        """match id -> clip ids, for a linked profile's match history."""
        if self.cfg.linked_profiles.get(game) != key or not self.stats:
            return {}
        clips = [c for c in self.clips.list() if self._stats_game_of(c["game_id"]) == game]
        out: dict[str, list[int]] = {}
        for m in self.stats.matches(game, key):
            m_start = iso_ts(m.date)
            for c in clips:
                c_start = c["created_at"] - (c["duration"] or 0)
                if m_start - 30 <= c["created_at"] and c_start <= m_start + m.duration_s + 30:
                    out.setdefault(m.id, []).append(c["id"])
        return out

    # ── goals ───────────────────────────────────────────────────────────────
    def _goal_context(self) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for game, key in self.cfg.linked_profiles.items():
            with contextlib.suppress(Exception):
                ov = self.stats.overview(game, key)
                rank = (ov["profile"].get("ranks") or [{}])[0]
                profiles[game] = {
                    "rank_value": rank.get("value"),
                    "rank_label": rank.get("label"),
                    "recent": [m.result for m in self.stats.matches(game, key)[:20]],
                }
        return {"daily": self.playtime.daily(8), "clips": self.clips.list(), "profiles": profiles, "now": time.time()}

    def goals_view(self) -> list[dict[str, Any]]:
        ctx = self._goal_context()
        return [{**g, "progress": evaluate(g, ctx)} for g in self.goals.all()]

    def check_goals(self) -> None:
        """Tell the player when a goal is reached, or a daily cap is close / blown."""
        for g in self.goals_view():
            state = g["progress"]["state"]
            if state == g["notified"]:
                continue
            if state in ("done", "over", "close"):
                self.events.publish("goal", goal=g, state=state)
            self.goals.mark(g["id"], notified=state, done=state == "done" if g["kind"] != "daily_cap" else None)

    # ── friends ─────────────────────────────────────────────────────────────
    def add_friend(self, game: str, query: str) -> dict[str, Any]:
        profile = self.stats.lookup(game, query)
        return self.friends.add(game, profile.key, profile.name)

    def refresh_friends(self, force: bool = False) -> int:
        todo = self.friends.friends if force else self.friends.due()
        for f in todo:
            with contextlib.suppress(Exception):
                self.stats.sync(f["game"], f["key"])
            self.friends.touched(f["game"], f["key"])
        return len(todo)

    def friends_view(self) -> dict[str, Any]:
        return friends_feed(self.friends.friends, self.stats)

    # ── session (for the in-game overlay) ───────────────────────────────────
    def session_view(self) -> dict[str, Any]:
        now = time.time()
        playing = self.playtime.now_playing()
        today = self.playtime.daily(1)[-1] if self.playtime.daily(1) else {"seconds": 0}
        current = playing[0] if playing else None
        out: dict[str, Any] = {"playing": current, "today_seconds": today["seconds"], "buffer": self.buffer.status()}
        if current:
            out["session_clips"] = sum(1 for c in self.clips.list(game_id=current["game_id"]) if c["created_at"] >= current["started_at"])
            game = self._stats_game_of(current["game_id"])
            key = self.cfg.linked_profiles.get(game or "")
            if self.stats and game and key:
                with contextlib.suppress(Exception):
                    todays = [m for m in self.stats.matches(game, key) if iso_ts(m.date) >= now - 86400 * 0.75]
                    out["today"] = {"wins": sum(m.won for m in todays), "losses": sum(m.result == "loss" for m in todays)}
                    rank = (self.stats.overview(game, key)["profile"].get("ranks") or [{}])[0]
                    out["rank"] = rank.get("label")
        caps = [g for g in self.goals_view() if g["kind"] == "daily_cap"]
        if caps:
            out["daily_cap"] = caps[0]["progress"]
        return out

    # ── auto-clip integrations ──────────────────────────────────────────────
    def autoclip_games(self) -> list[dict[str, Any]]:
        out = [
            {
                "game_id": "riot:league_of_legends",
                "name": "League of Legends",
                "method": "Riot Live Client Data API",
                "needs_install": False,
                "installed": True,
            }
        ]
        for gid, name in (("steam:570", "Dota 2"), ("steam:730", "Counter-Strike 2")):
            try:
                game = self.library.get(gid)
            except KeyError:
                continue
            out.append(
                {
                    "game_id": gid,
                    "name": name,
                    "method": "Valve Game State Integration",
                    "needs_install": True,
                    "installed": gsi_installed(gid, game.install_dir),
                }
            )
        return out

    def install_autoclip(self, game_id: str) -> str:
        game = self.library.get(game_id)
        return str(install_gsi(game_id, game.install_dir, self.gsi.token))

    # ── share & edit ────────────────────────────────────────────────────────
    def share_clip(self, clip_id: int) -> dict[str, Any]:
        clip = self.clips.get(clip_id)
        url = share.upload(Path(clip["path"]), self.cfg.share_host)
        return self.clips.set_share_url(clip_id, url)

    def _export(self, source: dict[str, Any], dest: Path, title: str) -> dict[str, Any]:
        clip = self.clips.add(
            dest, kind="export", game_id=source["game_id"], game_name=source["game_name"], title=title, parent_id=source["id"]
        )
        self.events.publish("clip_saved", clip=clip)
        return clip

    def make_montage(self, clip_ids: list[int], title: str | None = None) -> dict[str, Any]:
        sources = [self.clips.get(i) for i in clip_ids]
        first = sources[0]
        dest = ClipStore.new_path(self.clips_root, first["game_name"], "montage")
        edit.montage(sources, dest, encoder=self.cfg.encoder)
        return self._export(first, dest, title or f"Montage · {len(sources)} clips")

    def make_caption(self, clip_id: int, text: str, position: str = "bottom") -> dict[str, Any]:
        src = self.clips.get(clip_id)
        path = Path(src["path"])
        dest = path.with_name(f"{path.stem} (captioned){path.suffix}")
        edit.caption(path, dest, text, position=position, encoder=self.cfg.encoder)
        return self._export(src, dest, f"{src['title']} · “{text[:40]}”")

    def make_vertical(self, clip_id: int, mode: str = "blur") -> dict[str, Any]:
        src = self.clips.get(clip_id)
        path = Path(src["path"])
        dest = path.with_name(f"{path.stem} (vertical){path.suffix}")
        edit.vertical(path, dest, mode=mode, encoder=self.cfg.encoder)
        return self._export(src, dest, f"{src['title']} (9:16)")

    def make_without_mic(self, clip_id: int) -> dict[str, Any]:
        src = self.clips.get(clip_id)
        path = Path(src["path"])
        dest = path.with_name(f"{path.stem} (no mic){path.suffix}")
        edit.without_mic(path, dest)
        return self._export(src, dest, f"{src['title']} (no mic)")

    def shutdown(self) -> None:
        self._stop.set()
        self.autoclip.flush()
        self.league.stop()
        self.gsi.stop()
        self.playtime.stop()
        self.buffer.stop()
