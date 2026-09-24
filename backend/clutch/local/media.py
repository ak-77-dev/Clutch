"""Music: see and control what's playing (Spotify, Apple Music, YouTube Music, ...).

Windows keeps a list of media sessions for every app that plays audio through its
media controls, the same thing the volume flyout and keyboard media keys use:
Spotify, the Apple Music app, YouTube Music (the Chrome / Edge web app or a
browser tab), iTunes, Media Player and most others. Reading and controlling those
sessions (``GlobalSystemMediaTransportControlsSessionManager``) needs no account
or API key and works the same for every app.

Per-app volume goes through the audio mixer (Core Audio sessions), which is
per process: YouTube Music in Chrome shares its volume with the rest of Chrome.

Everything WinRT / COM runs on one worker thread with its own asyncio loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hashlib
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
REPEAT = {0: "none", 1: "track", 2: "list"}
ACTIONS = ("play_pause", "play", "pause", "next", "previous", "seek", "shuffle", "repeat")
YT_MUSIC_PWA = "cinhimbnkkaeohfgghhklpknlkffjgod"  # YouTube Music's Chrome web app id
BROWSERS = {"chrome": ("Chrome", "chrome.exe"), "msedge": ("Edge", "msedge.exe"), "firefox": ("Firefox", "firefox.exe"),
            "brave": ("Brave", "brave.exe"), "opera": ("Opera", "opera.exe"), "vivaldi": ("Vivaldi", "vivaldi.exe")}  # fmt: skip
MUSIC_APPS = {
    "spotify": {"name": "Spotify", "accent": "#1ed760", "store": ("SpotifyAB.SpotifyMusic", "Spotify"), "web": "https://open.spotify.com"},
    "applemusic": {
        "name": "Apple Music",
        "accent": "#fa2d48",
        "store": ("AppleInc.AppleMusicWin", "App"),
        "web": "https://music.apple.com",
    },
    "ytmusic": {"name": "YouTube Music", "accent": "#ff0033", "store": None, "web": "https://music.youtube.com"},
}


# ── who is playing ───────────────────────────────────────────────────────────


def identify(aumid: str | None) -> tuple[str, str]:
    """A media session's app id -> (app key, display name).

    'Spotify.exe' / 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' -> spotify;
    'AppleInc.AppleMusicWin_…!App' -> applemusic; 'Chrome._crx_cinhimbnkk…' (Windows
    shortens the web-app id) -> ytmusic; other browsers -> ('browser', 'Chrome').
    """
    a = (aumid or "").strip()
    low = a.lower()
    if "_crx_cinhimbnkk" in low:
        return "ytmusic", "YouTube Music"
    if "spotify" in low:
        return "spotify", "Spotify"
    if "applemusic" in low:
        return "applemusic", "Apple Music"
    if "itunes" in low:
        return "applemusic", "iTunes"
    if "zunemusic" in low:
        return "other", "Media Player"
    for key, (name, _) in BROWSERS.items():
        if low.startswith(key):
            return "browser", name
    base = a.split("!")[0].split("_")[0]
    base = base[:-4] if base.lower().endswith(".exe") else base
    return "other", base.rsplit(".", 1)[-1] or "Media"


def processes_for(aumid: str | None) -> tuple[str, ...]:
    """Process names whose audio belongs to this session (for the volume mixer)."""
    app, _ = identify(aumid)
    low = (aumid or "").lower()
    if app == "spotify":
        return ("spotify.exe",)
    if app == "applemusic":
        return ("applemusic.exe", "itunes.exe")
    for key, (_, exe) in BROWSERS.items():
        if low.startswith(key):
            return (exe,)
    if low.endswith(".exe"):
        return (low,)
    return ()


def art_key(aumid: str, title: str, artist: str) -> str:
    return hashlib.sha1(f"{aumid}|{title}|{artist}".encode()).hexdigest()[:16]


# ── installed music apps & launching ────────────────────────────────────────


def _store_family(prefix: str) -> str | None:
    """'SpotifyAB.SpotifyMusic' -> 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0' if the Store app is installed."""
    if sys.platform != "win32":
        return None
    import winreg

    path = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    return None
                if name.startswith(prefix + "_"):
                    return f"{prefix}_{name.rsplit('__', 1)[-1]}"  # full name -> family name (publisher hash)
                i += 1
    except OSError:
        return None


def _ytmusic_web_app() -> tuple[str, str] | None:
    """(chrome_proxy.exe, profile folder) when YouTube Music is installed as a Chrome app."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    user_data = Path(local) / "Google" / "Chrome" / "User Data"
    proxy = next(
        (
            p
            for p in (
                Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome_proxy.exe"
                for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            )
            if p.is_file()
        ),
        None,
    )
    if proxy is None or not user_data.is_dir():
        return None
    for profile in sorted(user_data.iterdir()):
        if (profile / "Web Applications" / "Manifest Resources" / YT_MUSIC_PWA).is_dir():
            return str(proxy), profile.name
    return None


def _installed(key: str) -> bool:
    app = MUSIC_APPS[key]
    if app["store"] and _store_family(app["store"][0]):
        return True
    if key == "spotify":
        return (Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe").is_file()
    if key == "ytmusic":
        return _ytmusic_web_app() is not None
    return False


def music_apps() -> list[dict[str, Any]]:
    out = []
    for key, app in MUSIC_APPS.items():
        installed = _installed(key)
        out.append(
            {"key": key, "name": app["name"], "accent": app["accent"], "installed": installed, "opens": "app" if installed else "web"}
        )
    return out


def launch_music_app(key: str) -> str:
    """Open a music app (or its website when it isn't installed). Returns how it was opened."""
    app = MUSIC_APPS[key]  # KeyError for unknown apps
    if app["store"]:
        family = _store_family(app["store"][0])
        if family:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{family}!{app['store'][1]}"])
            return "app"
    if key == "spotify":
        exe = Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe"
        if exe.is_file():
            subprocess.Popen([str(exe)])
            return "app"
    if key == "ytmusic":
        pwa = _ytmusic_web_app()
        if pwa:
            subprocess.Popen([pwa[0], f"--profile-directory={pwa[1]}", f"--app-id={YT_MUSIC_PWA}"])
            return "app"
    os.startfile(app["web"])  # type: ignore[attr-defined]  # Windows only
    return "web"


# ── volume mixer ─────────────────────────────────────────────────────────────


class Mixer:
    """Per-process volume via Core Audio sessions (pycaw). Lives on its own COM thread."""

    def __init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="clutch-mixer", initializer=self._init)

    @staticmethod
    def _init() -> None:
        with contextlib.suppress(Exception):
            import comtypes

            comtypes.CoInitialize()

    @staticmethod
    def _sessions(names: tuple[str, ...]) -> list[Any]:
        from pycaw.pycaw import AudioUtilities

        wanted = {n.lower() for n in names}
        return [s for s in AudioUtilities.GetAllSessions() if s.Process and s.Process.name().lower() in wanted]

    def _read(self, names: tuple[str, ...]) -> dict[str, Any] | None:
        for s in self._sessions(names):
            v = s.SimpleAudioVolume
            return {"level": round(float(v.GetMasterVolume()), 3), "muted": bool(v.GetMute())}
        return None

    def _write(self, names: tuple[str, ...], level: float | None, muted: bool | None) -> bool:
        sessions = self._sessions(names)
        for s in sessions:
            v = s.SimpleAudioVolume
            if level is not None:
                v.SetMasterVolume(max(0.0, min(1.0, float(level))), None)
            if muted is not None:
                v.SetMute(int(bool(muted)), None)
        return bool(sessions)

    def read(self, names: tuple[str, ...]) -> dict[str, Any] | None:
        if not names:
            return None
        try:
            return self._pool.submit(self._read, names).result(timeout=3)
        except Exception:
            return None

    def write(self, names: tuple[str, ...], level: float | None = None, muted: bool | None = None) -> bool:
        if not names:
            return False
        try:
            return self._pool.submit(self._write, names, level, muted).result(timeout=3)
        except Exception:
            return False

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ── media sessions (WinRT) ───────────────────────────────────────────────────


class SmtcBackend:
    """Talks to Windows' media session manager. Only used from the media thread's loop."""

    def __init__(self) -> None:
        self.mgr: Any = None

    async def open(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        self.mgr = await Manager.request_async()

    def _session(self, session_id: str | None) -> Any:
        sessions = list(self.mgr.get_sessions())
        if session_id:
            return next((s for s in sessions if s.source_app_user_model_id == session_id), None)
        return self.mgr.get_current_session() or (sessions[0] if sessions else None)

    async def snapshot(self) -> tuple[str | None, list[dict[str, Any]]]:
        current = self.mgr.get_current_session()
        out = []
        for s in list(self.mgr.get_sessions()):
            try:
                props = await s.try_get_media_properties_async()
            except Exception:  # an app that's closing mid-read
                continue
            info = s.get_playback_info()
            tl = s.get_timeline_properties()
            c = info.controls
            status = STATUS.get(int(info.playback_status), "stopped")
            position = tl.position.total_seconds() if tl.position else 0.0
            if status == "playing" and tl.last_updated_time:  # the position is as of the last update
                with contextlib.suppress(Exception):
                    position += max(0.0, (datetime.now(timezone.utc) - tl.last_updated_time).total_seconds())
            duration = (tl.end_time - tl.start_time).total_seconds() if tl.end_time else 0.0
            out.append(
                {
                    "id": s.source_app_user_model_id,
                    "title": props.title or "",
                    "artist": props.artist or props.album_artist or "",
                    "album": props.album_title or "",
                    "status": status,
                    "position": round(min(position, duration) if duration else position, 2),
                    "duration": round(max(0.0, duration), 2),
                    "can": {
                        "play_pause": bool(c.is_play_pause_toggle_enabled or c.is_play_enabled or c.is_pause_enabled),
                        "next": bool(c.is_next_enabled),
                        "previous": bool(c.is_previous_enabled),
                        "seek": bool(c.is_playback_position_enabled) and duration > 0,
                        "shuffle": bool(c.is_shuffle_enabled) and info.is_shuffle_active is not None,
                        "repeat": bool(c.is_repeat_enabled) and info.auto_repeat_mode is not None,
                    },
                    "shuffle": info.is_shuffle_active,
                    "repeat": REPEAT.get(int(info.auto_repeat_mode)) if info.auto_repeat_mode is not None else None,
                    "has_art": props.thumbnail is not None,
                }
            )
        return (current.source_app_user_model_id if current else None), out

    async def thumbnail(self, session_id: str) -> tuple[bytes, str] | None:
        from winrt.windows.storage.streams import Buffer, InputStreamOptions

        s = self._session(session_id)
        if s is None:
            return None
        props = await s.try_get_media_properties_async()
        if props.thumbnail is None:
            return None
        stream = await props.thumbnail.open_read_async()
        size = min(int(stream.size), 8 * 1024 * 1024)
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        return bytes(buf), stream.content_type or "image/png"

    async def control(self, session_id: str | None, action: str, value: Any = None) -> bool:
        s = self._session(session_id)
        if s is None:
            return False
        if action == "play_pause":
            return bool(await s.try_toggle_play_pause_async())
        if action == "play":
            return bool(await s.try_play_async())
        if action == "pause":
            return bool(await s.try_pause_async())
        if action == "next":
            return bool(await s.try_skip_next_async())
        if action == "previous":
            return bool(await s.try_skip_previous_async())
        if action == "seek":
            start = s.get_timeline_properties().start_time.total_seconds()
            return bool(await s.try_change_playback_position_async(int((start + float(value)) * 10_000_000)))
        if action == "shuffle":
            return bool(await s.try_change_shuffle_active_async(bool(value)))
        if action == "repeat":
            from winrt.windows.media import MediaPlaybackAutoRepeatMode

            mode = {"none": 0, "track": 1, "list": 2}[str(value)]
            return bool(await s.try_change_auto_repeat_mode_async(MediaPlaybackAutoRepeatMode(mode)))
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")


def _expectation(action: str, value: Any, before: dict[str, Any] | None) -> Any:
    """What a session looks like once ``action`` has taken effect."""
    was = before or {}
    return {
        "play_pause": lambda s: s["status"] != was.get("status"),
        "play": lambda s: s["status"] == "playing",
        "pause": lambda s: s["status"] != "playing",
        "next": lambda s: (s["title"], s["artist"]) != (was.get("title"), was.get("artist")),
        "previous": lambda s: (s["title"], s["artist"]) != (was.get("title"), was.get("artist")) or s["position"] < 3,
        "seek": lambda s: abs(s["position"] - float(value)) < 3,
        "shuffle": lambda s: s["shuffle"] == bool(value),
        "repeat": lambda s: s["repeat"] == value,
    }[action]


class MediaService:
    """Keeps a snapshot of the media sessions, publishes changes, runs commands."""

    POLL_S = 1.0
    # While an app switches tracks its session can vanish, or report no title, for a
    # moment (YouTube Music does, for ~0.5 s). Showing that makes the player flicker, so
    # a session that was there a moment ago is held for this long.
    GRACE_S = 3.0
    # Apps can update the title before the artwork: artwork identical to the previous
    # track's is re-read a few times before it's believed.
    ART_RETRIES = 4

    def __init__(self, events: Any = None, *, backend: Any = None, mixer: Mixer | None = None) -> None:
        self.events = events
        self._backend = backend
        self.mixer = mixer
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._state: dict[str, Any] = {"available": False, "current": None, "sessions": [], "at": time.time()}
        self._sig: Any = None
        self._art: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._held: dict[str, tuple[dict[str, Any], float]] = {}  # session id -> (last good session, seen at)
        self._last_art: dict[str, tuple[str, str]] = {}  # session id -> (art key, digest) of its last track
        self._art_tries: dict[str, int] = {}
        self._ducked: dict[tuple[str, ...], float] = {}
        self.error: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> bool:
        if self._thread:
            return True
        if self._backend is None:
            try:
                import winrt.windows.media.control  # noqa: F401
            except Exception as exc:  # not Windows, or the WinRT bindings aren't installed
                self.error = f"media controls unavailable: {exc}"
                return False
            self._backend = SmtcBackend()
        if self.mixer is None:
            try:
                import pycaw  # noqa: F401

                self.mixer = Mixer()
            except Exception:
                self.mixer = None
        self._thread = threading.Thread(target=self._run, name="clutch-media", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        return self._state["available"]

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            self.restore()
        if self.mixer:
            self.mixer.close()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    async def _main(self) -> None:
        try:
            if hasattr(self._backend, "open"):
                await self._backend.open()
            self._state["available"] = True
        except Exception as exc:
            self.error = f"media controls unavailable: {exc}"
            self._ready.set()
            return
        await self._refresh()
        self._ready.set()
        while not self._stop.is_set():
            await asyncio.sleep(self.POLL_S)
            with contextlib.suppress(Exception):
                await self._refresh()

    # ── snapshot ────────────────────────────────────────────────────────────
    async def _art_for(self, s: dict[str, Any]) -> str | None:
        """The artwork key to show for a session, reading (and caching) its thumbnail when needed."""
        if not s.get("has_art"):
            return None
        key = art_key(s["id"], s["title"], s["artist"])
        if key in self._art:
            return key
        try:
            art = await self._backend.thumbnail(s["id"])
        except Exception:
            art = None
        if not art:
            return None
        digest = hashlib.sha1(art[0]).hexdigest()
        prev_key, prev_digest = self._last_art.get(s["id"], (None, None))
        if prev_key and prev_key != key and digest == prev_digest and self._art_tries.get(key, 0) < self.ART_RETRIES:
            # Probably the old track's image still: keep showing it and look again next poll.
            self._art_tries[key] = self._art_tries.get(key, 0) + 1
            return prev_key if prev_key in self._art else None
        self._art[key] = art
        self._art_tries.pop(key, None)
        self._last_art[s["id"]] = (key, digest)
        while len(self._art) > 40:
            self._art.popitem(last=False)
        return key

    async def _refresh(self) -> dict[str, Any]:
        current, raw = await self._backend.snapshot()
        now = time.time()
        sessions = []
        seen = set()
        for s in raw:
            held = self._held.get(s["id"])
            if not s["title"] and held and now - held[1] < self.GRACE_S:
                sessions.append(held[0])  # mid track change: keep showing the last track
                seen.add(s["id"])
                continue
            app, name = identify(s["id"])
            procs = processes_for(s["id"])
            key = await self._art_for(s)
            volume = self.mixer.read(procs) if self.mixer else None
            session = {
                **{k: v for k, v in s.items() if k != "has_art"},
                "app": app,
                "app_name": name,
                "art": key,
                "volume": volume,
                "volume_scope": "browser" if app in ("ytmusic", "browser") else "app",
            }
            sessions.append(session)
            seen.add(s["id"])
            if s["title"]:
                self._held[s["id"]] = (session, now)
        for sid, (session, at) in list(self._held.items()):
            if sid in seen:
                continue
            if now - at < self.GRACE_S:
                sessions.append(session)  # vanished for a moment (track change): hold it
            else:
                del self._held[sid]
        # Playing sessions first, then music apps, so "the" session is the one you'd expect.
        sessions.sort(key=lambda x: (x["status"] != "playing", x["app"] not in MUSIC_APPS, x["id"] != current))
        state = {"available": True, "current": current, "sessions": sessions, "at": time.time()}
        self._state = state
        sig = [
            (
                x["id"],
                x["title"],
                x["artist"],
                x["status"],
                x["shuffle"],
                x["repeat"],
                x["art"],
                (x["volume"] or {}).get("level"),
                (x["volume"] or {}).get("muted"),
            )
            for x in sessions
        ]
        if sig != self._sig:
            self._sig = sig
            if self.events:
                self.events.publish("media", **state)
        return state

    def state(self) -> dict[str, Any]:
        return self._state if self._state["available"] else {**self._state, "error": self.error}

    def now_playing(self) -> dict[str, Any] | None:
        """The session to show in compact places (overlay, rail): playing first, else paused."""
        sessions = [s for s in self._state["sessions"] if s["title"]]
        return sessions[0] if sessions else None

    def art(self, key: str) -> tuple[bytes, str] | None:
        return self._art.get(key)

    # ── commands ────────────────────────────────────────────────────────────
    def _require(self) -> None:
        if not (self._loop and self._backend and self._state["available"]):
            raise RuntimeError(self.error or "Media controls aren't available")

    def _call(self, coro: Any, timeout: float = 5) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def command(self, action: str, session: str | None = None, value: Any = None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        if action == "seek" and (value is None or float(value) < 0):
            raise ValueError("seek needs a position in seconds")
        if action == "repeat" and value not in ("none", "track", "list"):
            raise ValueError("repeat must be none, track or list")
        self._require()
        before = next((x for x in self._state["sessions"] if x["id"] == session), None) if session else self.now_playing()
        target = session or (before or {}).get("id")
        ok = self._call(self._backend.control(target, action, value))
        done = _expectation(action, value, before)

        async def settle() -> dict[str, Any]:
            # Apps take 0.1-1 s to reflect a command. Answer as soon as the change shows,
            # so the UI never flashes back to the old state.
            state = self._state
            for _ in range(10):
                await asyncio.sleep(0.15)
                state = await self._refresh()
                now = next((x for x in state["sessions"] if x["id"] == target), None)
                if now is not None and done(now):
                    break
            return state

        state = self._call(settle(), timeout=6)
        return {"ok": ok, **state}

    def set_volume(self, session: str, level: float | None = None, muted: bool | None = None) -> dict[str, Any]:
        if level is not None and not 0 <= float(level) <= 1:
            raise ValueError("level is 0..1")
        self._require()
        procs = processes_for(session)
        if not (self.mixer and procs):
            raise ValueError("Volume can't be set for this app")
        ok = self.mixer.write(procs, level, muted)
        return {"ok": ok, **self._call(self._refresh())}

    # ── ducking while gaming ────────────────────────────────────────────────
    def duck(self, level: float) -> int:
        """Turn music apps down to ``level`` (0..1), remembering where they were."""
        if not self.mixer:
            return 0
        n = 0
        for s in self._state["sessions"]:
            if s["app"] not in MUSIC_APPS:
                continue
            procs = processes_for(s["id"])
            if not procs or procs in self._ducked:
                continue
            now = self.mixer.read(procs)
            if now and now["level"] > level:
                self._ducked[procs] = now["level"]
                self.mixer.write(procs, level=level)
                n += 1
        return n

    def restore(self) -> int:
        if not self.mixer:
            return 0
        n = 0
        for procs, level in list(self._ducked.items()):
            self.mixer.write(procs, level=level)
            del self._ducked[procs]
            n += 1
        return n
