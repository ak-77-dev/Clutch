"""Music controls: session identification, snapshots, commands, volume and ducking.

Runs against a fake media backend and mixer, so no real player is ever touched.
"""

from __future__ import annotations

import time

import pytest

from clutch.local.media import MediaService, art_key, identify, processes_for

YT = "Chrome._crx_cinhimbnkkghhklpknlkffjgod"
SPOTIFY = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"


def test_identify_music_apps_and_browsers():
    assert identify(YT) == ("ytmusic", "YouTube Music")
    assert identify("Spotify.exe") == ("spotify", "Spotify") and identify(SPOTIFY)[0] == "spotify"
    assert identify("AppleInc.AppleMusicWin_nzyj5cx40ttqa!App") == ("applemusic", "Apple Music")
    assert identify("MSEdge") == ("browser", "Edge") and identify("Chrome") == ("browser", "Chrome")
    assert identify("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic") == ("other", "Media Player")
    assert identify("foobar2000.exe") == ("other", "foobar2000")
    assert processes_for(YT) == ("chrome.exe",) and processes_for("Spotify.exe") == ("spotify.exe",)
    assert processes_for("AppleInc.AppleMusicWin_nzyj5cx40ttqa!App") == ("applemusic.exe", "itunes.exe")
    assert processes_for("aimp.exe") == ("aimp.exe",) and processes_for("Some.Store.App!App") == ()


class FakeBackend:
    def __init__(self) -> None:
        self.sessions = [
            {"id": "Chrome", "title": "", "artist": "", "album": "", "status": "paused", "position": 0, "duration": 0, "can": {}, "shuffle": None, "repeat": None, "has_art": False},
            {"id": SPOTIFY, "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours", "status": "playing", "position": 42.0, "duration": 200.0,
             "can": {"play_pause": True, "next": True, "previous": True, "seek": True, "shuffle": True, "repeat": True}, "shuffle": False, "repeat": "none", "has_art": True},
        ]  # fmt: skip
        self.calls: list[tuple] = []

    async def snapshot(self):
        return SPOTIFY, [dict(s) for s in self.sessions]

    async def thumbnail(self, session_id):
        return b"\x89PNG-art", "image/png"

    async def control(self, session_id, action, value=None):
        self.calls.append((session_id, action, value))
        if action == "play_pause":
            s = next(x for x in self.sessions if x["id"] == session_id)
            s["status"] = "paused" if s["status"] == "playing" else "playing"
        return True


class FakeMixer:
    def __init__(self) -> None:
        self.levels = {("spotify.exe",): {"level": 0.8, "muted": False}}
        self.writes: list[tuple] = []

    def read(self, names):
        return self.levels.get(names)

    def write(self, names, level=None, muted=None):
        self.writes.append((names, level, muted))
        if names in self.levels and level is not None:
            self.levels[names] = {**self.levels[names], "level": level}
        return names in self.levels

    def close(self):
        pass


class Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, type_, **data):
        self.events.append({"type": type_, **data})


@pytest.fixture
def media():
    bus = Bus()
    m = MediaService(bus, backend=FakeBackend(), mixer=FakeMixer())
    m.POLL_S = 0.05
    assert m.start()
    yield m, bus
    m.stop()


def test_snapshot_puts_the_playing_music_app_first_with_art_and_volume(media):
    m, bus = media
    st = m.state()
    top = st["sessions"][0]
    assert st["available"] and top["app"] == "spotify" and top["title"] == "Blinding Lights"
    assert top["art"] == art_key(SPOTIFY, "Blinding Lights", "The Weeknd") and m.art(top["art"]) == (b"\x89PNG-art", "image/png")
    assert top["volume"] == {"level": 0.8, "muted": False} and top["volume_scope"] == "app"
    assert m.now_playing()["title"] == "Blinding Lights"  # the empty Chrome session isn't "now playing"
    first = len(bus.events)
    time.sleep(0.2)  # several polls with nothing changed
    assert len(bus.events) == first, "unchanged state isn't re-published"


def test_commands_target_now_playing_and_publish_the_change(media):
    m, bus = media
    before = len(bus.events)
    out = m.command("play_pause")
    assert out["ok"] and m._backend.calls[-1] == (SPOTIFY, "play_pause", None)
    assert out["sessions"][0]["status"] == "paused" or any(s["status"] == "paused" for s in out["sessions"])
    assert len(bus.events) > before and bus.events[-1]["type"] == "media"
    m.command("seek", value=90)
    assert m._backend.calls[-1] == (SPOTIFY, "seek", 90)
    m.command("next", session="Chrome")
    assert m._backend.calls[-1] == ("Chrome", "next", None)


def test_command_validation(media):
    m, _ = media
    with pytest.raises(ValueError):
        m.command("explode")
    with pytest.raises(ValueError):
        m.command("seek")
    with pytest.raises(ValueError):
        m.command("repeat", value="forever")
    with pytest.raises(ValueError):
        m.set_volume(SPOTIFY, level=1.5)
    with pytest.raises(ValueError):
        m.set_volume("Some.Store.App!App", level=0.5)  # no process to control


def test_volume_and_ducking_while_gaming(media):
    m, _ = media
    m.set_volume(SPOTIFY, level=0.6)
    assert m.mixer.levels[("spotify.exe",)]["level"] == 0.6
    assert m.duck(0.25) == 1 and m.mixer.levels[("spotify.exe",)]["level"] == 0.25
    assert m.duck(0.25) == 0, "ducking twice doesn't overwrite the saved level"
    assert m.restore() == 1 and m.mixer.levels[("spotify.exe",)]["level"] == 0.6
    assert m.restore() == 0


def test_unavailable_without_a_backend(monkeypatch):
    m = MediaService(None, backend=None)
    monkeypatch.setitem(__import__("sys").modules, "winrt.windows.media.control", None)
    assert m.start() is False and m.state()["available"] is False
    with pytest.raises(RuntimeError):
        m.command("next")


def test_unavailable_state_explains_why(monkeypatch):
    m = MediaService(None, backend=None)
    assert "error" in m.state() and m.state()["error"] is None  # still starting: no reason yet
    monkeypatch.setitem(__import__("sys").modules, "winrt.windows.media.control", None)
    m.start()
    assert m.state()["error"].startswith("media controls unavailable")


class ScriptedBackend:
    """Replays a list of snapshots, one per refresh, and serves artwork per title."""

    def __init__(self, frames, art):
        self.frames, self.art, self.i = frames, art, 0

    async def snapshot(self):
        frame = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return (frame[0]["id"] if frame else None), [dict(s) for s in frame]

    async def thumbnail(self, session_id):
        return self.art(self.i), "image/png"


def _yt(title, status="playing"):
    return {"id": YT, "title": title, "artist": "Sleep Theory", "album": "Paper Hearts", "status": status, "position": 1.0, "duration": 200.0,
            "can": {"play_pause": True}, "shuffle": None, "repeat": None, "has_art": True}  # fmt: skip


def test_track_changes_dont_flicker_the_player(monkeypatch):
    import asyncio

    frames = [[_yt("Enough")], [], [{**_yt(""), "has_art": False}], [_yt("Another Way")]]
    m = MediaService(None, backend=ScriptedBackend(frames, lambda i: b"art-" + str(i).encode()))
    titles = [[s["title"] for s in asyncio.run(m._refresh())["sessions"]] for _ in frames]
    # The session vanished and then came back untitled for a moment; the player kept showing the last track.
    assert titles == [["Enough"], ["Enough"], ["Enough"], ["Another Way"]]

    clock = [1000.0]
    monkeypatch.setattr("clutch.local.media.time.time", lambda: clock[0])
    m2 = MediaService(None, backend=ScriptedBackend([[_yt("Enough")], []], lambda i: b"x"))
    asyncio.run(m2._refresh())
    clock[0] += m2.GRACE_S + 0.1
    assert asyncio.run(m2._refresh())["sessions"] == [], "a session that's really gone disappears after the grace period"


def test_old_artwork_under_a_new_title_is_re_read():
    import asyncio

    frames = [[_yt("Enough")]] + [[_yt("Another Way")]] * 6
    # The app keeps serving the old image for two more polls, then the new one.
    m = MediaService(None, backend=ScriptedBackend(frames, lambda i: b"old" if i <= 3 else b"new"))
    first = asyncio.run(m._refresh())["sessions"][0]["art"]
    shown = [asyncio.run(m._refresh())["sessions"][0]["art"] for _ in range(3)]
    new_key = art_key(YT, "Another Way", "Sleep Theory")
    assert shown[0] == first and shown[1] == first, "keeps the previous image while the app catches up"
    assert shown[2] == new_key and m.art(new_key)[0] == b"new"


def test_same_album_art_is_accepted_after_a_few_looks():
    import asyncio

    frames = [[_yt("Enough")]] + [[_yt("Another Way")]] * 8
    m = MediaService(None, backend=ScriptedBackend(frames, lambda i: b"album-cover"))
    asyncio.run(m._refresh())
    keys = [asyncio.run(m._refresh())["sessions"][0]["art"] for _ in range(m.ART_RETRIES + 1)]
    assert keys[-1] == art_key(YT, "Another Way", "Sleep Theory")  # tracks from one album really do share art
