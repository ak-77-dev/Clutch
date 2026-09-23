"""Desktop features: launcher scanning, playtime, the audio timeline, the clip store and the API guard."""

from __future__ import annotations

import json
import os
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clutch.local import library as lib
from clutch.local.capture import AudioTrack, ffmpeg_exe, media_info
from clutch.local.clips import ClipStore
from clutch.local.config import SettingsStore
from clutch.local.playtime import MIN_SESSION_S, PlaytimeTracker, ProcessMatcher, stats_game_for

# ── launcher scanning ────────────────────────────────────────────────────────


def test_vdf_parser_handles_nesting_and_escapes():
    text = r""""libraryfolders" { "0" { "path" "C:\\Games\\Steam" "apps" { "570" "123" } } }"""
    data = lib.parse_vdf(text)
    assert data["libraryfolders"]["0"]["path"] == r"C:\Games\Steam"
    assert data["libraryfolders"]["0"]["apps"] == {"570": "123"}


def test_scan_steam(tmp_path):
    lib_dir = tmp_path / "lib"
    (lib_dir / "steamapps" / "common" / "dota 2 beta").mkdir(parents=True)
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" {{ "0" {{ "path" "{}" }} }}'.format(str(lib_dir).replace("\\", "\\\\")), encoding="utf-8"
    )
    for appid, name, folder in (("570", "Dota 2", "dota 2 beta"), ("228980", "Steamworks Common Redistributables", "x")):
        (lib_dir / "steamapps" / f"appmanifest_{appid}.acf").write_text(
            f'"AppState" {{ "appid" "{appid}" "name" "{name}" "installdir" "{folder}" "LastPlayed" "1700000000" }}', encoding="utf-8"
        )
    games = lib.scan_steam(steam)
    assert [g.id for g in games] == ["steam:570"]  # redistributables are skipped
    assert games[0].launch == {"uri": "steam://rungameid/570"} and games[0].last_played == 1700000000


def test_scan_epic_skips_dlc(tmp_path):
    for i, (app, main, name) in enumerate((("Fortnite", "Fortnite", "Fortnite"), ("DLC1", "Fortnite", "Some DLC"))):
        (tmp_path / f"{i}.item").write_text(
            json.dumps(
                {
                    "DisplayName": name,
                    "AppName": app,
                    "MainGameAppName": main,
                    "CatalogNamespace": "fn",
                    "CatalogItemId": "abc",
                    "InstallLocation": "E:\\Fortnite",
                    "LaunchExecutable": "FortniteLauncher.exe",
                    "bIsApplication": True,
                }
            ),
            encoding="utf-8",
        )
    games = lib.scan_epic(tmp_path)
    assert [g.name for g in games] == ["Fortnite"]
    assert games[0].launch["uri"] == "com.epicgames.launcher://apps/fn%3Aabc%3AFortnite?action=launch&silent=true"


def test_scan_riot(tmp_path):
    (tmp_path / "Metadata" / "valorant.live").mkdir(parents=True)
    (tmp_path / "RiotClientInstalls.json").write_text(json.dumps({"rc_default": "C:/Riot Games/Riot Client/RiotClientServices.exe"}))
    (tmp_path / "Metadata" / "valorant.live" / "valorant.live.product_settings.yaml").write_text(
        'product_install_full_path: "C:/Riot Games/VALORANT/live"\n', encoding="utf-8"
    )
    [g] = lib.scan_riot(tmp_path)
    assert g.id == "riot:valorant" and g.install_dir == "C:/Riot Games/VALORANT/live"
    assert lib.launch_command(g)[1:] == ["--launch-product=valorant", "--launch-patchline=live"]


def test_scan_xbox_skips_packages_without_executables(tmp_path):
    game = tmp_path / "Goat" / "Content"
    dlc = tmp_path / "DLC" / "Content"
    game.mkdir(parents=True)
    dlc.mkdir(parents=True)
    (game / "MicrosoftGame.config").write_text(
        '<Game><Identity Name="Coffee.Goat"/><ShellVisuals DefaultDisplayName="Goat Simulator 3" Square150x150Logo="Logo.png"/>'
        '<ExecutableList><Executable Name="Goat.exe" Id="App"/></ExecutableList></Game>',
        encoding="utf-8",
    )
    (dlc / "MicrosoftGame.config").write_text('<Game><Identity Name="Coffee.GoatDLC"/></Game>', encoding="utf-8")
    [g] = lib.scan_xbox([tmp_path])
    assert g.id == "xbox:Coffee.Goat" and g.name == "Goat Simulator 3" and g.icon_path.endswith("Logo.png")
    assert "Get-AppxPackageManifest" in lib.launch_command(g)[-1]


@pytest.mark.parametrize(
    ("entry", "expected_id", "launch"),
    [
        (
            {
                "DisplayName": "Call of Duty",
                "UninstallString": '"C:\\ProgramData\\Battle.net\\Agent\\Blizzard Uninstaller.exe" --lang=enUS --uid=auks --displayname="Call of Duty"',
                "InstallLocation": "E:\\Call of Duty",
                "DisplayIcon": "E:\\Call of Duty\\cod.exe",
            },
            "bnet:auks",
            ["C:\\BNet\\Battle.net.exe", "--exec=launch AUKS"],
        ),
        (
            {
                "DisplayName": "Far Cry\u00ae 5",
                "UninstallString": '"C:\\upc.exe" uplay://uninstall/1803',
                "InstallLocation": "C:/Games/Far Cry 5/",
                "DisplayIcon": "C:/x.ico",
            },
            "ubisoft:1803",
            "uplay://launch/1803/0",
        ),
        (
            {
                "DisplayName": "Titanfall\u2122 2",
                "UninstallString": '"C:\\Common Files\\EAInstaller\\Titanfall2\\Cleanup.exe" uninstall_game',
                "InstallLocation": "E:\\Titanfall2\\",
                "DisplayIcon": '"E:\\Titanfall2\\Titanfall2.exe"',
            },
            "ea:titanfall-2",
            ["E:\\Titanfall2\\Titanfall2.exe"],
        ),
    ],
)
def test_classify_uninstall_entries(entry, expected_id, launch):
    g = lib.classify_uninstall(entry, battlenet_exe="C:\\BNet\\Battle.net.exe")
    assert g.id == expected_id and "\u2122" not in g.name and "\u00ae" not in g.name
    assert lib.launch_command(g) == launch
    assert lib.classify_uninstall({"DisplayName": "Some App", "UninstallString": "msiexec /x {GUID}"}) is None
    assert lib.classify_uninstall({"DisplayName": "Battle.net", "UninstallString": "Blizzard Uninstaller.exe --uid=battle.net"}) is None


def test_library_dedupes_hides_and_drops_missing_installs(tmp_path):
    real = tmp_path / "Valorant"
    real.mkdir()
    a = lib.Game("riot:valorant", "VALORANT", "riot", str(real), {"uri": "x"})
    dup = lib.Game("reg:valorant", "VALORANT", "registry", str(real) + os.sep, {"uri": "y"})  # same folder, other source
    gone = lib.Game("steam:1", "Uninstalled", "steam", str(tmp_path / "missing"), {"uri": "z"})
    library = lib.Library(tmp_path / "lib.json", scanners=[lambda: [a], lambda: [dup, gone], lambda: 1 / 0])
    assert [g.id for g in library.scan()] == ["riot:valorant"]
    library.set_hidden("riot:valorant", True)
    assert library.all() == [] and len(library.all(include_hidden=True)) == 1
    reloaded = lib.Library(tmp_path / "lib.json", scanners=[])
    assert reloaded.hidden == {"riot:valorant"}
    with pytest.raises(ValueError):
        library.add_custom("Notes", str(tmp_path / "notes.txt"))


def test_known_non_games_start_hidden_but_can_be_unhidden(tmp_path):
    (tmp_path / "we").mkdir()
    wallpaper = lib.Game("steam:431960", "Wallpaper Engine", "steam", str(tmp_path / "we"), {"uri": "x"})
    library = lib.Library(tmp_path / "lib.json", scanners=[lambda: [wallpaper]])
    assert library.scan() == [] and library.hidden == {"steam:431960"}
    library.set_hidden("steam:431960", False)
    assert [g.id for g in library.scan()] == ["steam:431960"]  # the player's choice survives rescans


# ── playtime ────────────────────────────────────────────────────────────────


def test_process_matcher_prefers_the_most_specific_folder(tmp_path):
    outer = lib.Game("a", "Launcher", "custom", str(tmp_path / "Riot Games"), {})
    inner = lib.Game("b", "VALORANT", "riot", str(tmp_path / "Riot Games" / "VALORANT" / "live"), {})
    m = ProcessMatcher([outer, inner])
    assert m.match(str(tmp_path / "Riot Games" / "VALORANT" / "live" / "ShooterGame" / "VALORANT.exe")) is inner
    assert m.match(str(tmp_path / "Riot Games" / "Riot Client" / "RiotClientServices.exe")) is outer
    assert m.match(str(tmp_path / "Elsewhere" / "app.exe")) is None


def test_playtime_sessions_events_and_daily_split(tmp_path):
    game = lib.Game("steam:570", "Dota 2", "steam", str(tmp_path / "dota"), {})
    exe = str(tmp_path / "dota" / "dota2.exe")
    clock = {"t": 1_700_000_000.0}
    procs: set[str] = set()
    started, stopped = [], []
    tracker = PlaytimeTracker(tmp_path / "p.db", lambda: [game], list_processes=lambda: procs, clock=lambda: clock["t"])
    tracker.on_start.append(started.append)
    tracker.on_stop.append(lambda g, info: stopped.append(info))

    procs.add(exe)
    tracker.poll()
    assert [g.id for g in started] == ["steam:570"] and tracker.now_playing()[0]["game_name"] == "Dota 2"
    clock["t"] += 3600
    tracker.poll()
    procs.clear()
    clock["t"] += 5
    tracker.poll()
    assert stopped and stopped[0]["seconds"] == pytest.approx(3605)
    [summary] = tracker.summary()
    assert summary["sessions"] == 1 and summary["seconds"] == pytest.approx(3605)

    # A splash screen that closes within a minute isn't a session.
    procs.add(exe)
    tracker.poll()
    clock["t"] += MIN_SESSION_S - 1
    procs.clear()
    tracker.poll()
    assert tracker.summary()[0]["sessions"] == 1
    assert sum(d["seconds"] for d in tracker.daily(7)) == pytest.approx(3605, abs=1)
    assert stats_game_for(game) == "dota2"


# ── audio timeline ───────────────────────────────────────────────────────────


def _samples(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels()) == (48000, 2)  # clips always get 48 kHz stereo
        return memoryview(w.readframes(w.getnframes())).cast("h")


def test_audio_track_fills_gaps_and_extracts_by_wall_clock(tmp_path):
    track = AudioTrack("system", tmp_path)  # 48 kHz stereo, like most devices
    tone = (1000).to_bytes(2, "little", signed=True) * (24_000 * 2)  # 0.5 s of a constant sample
    track.write(100.5, tone)  # covers 100.0-100.5
    track.fill_silence(101.5)  # nothing playing for a second
    track.write(102.0, tone)  # covers 101.5-102.0
    s = _samples(track.extract(100.25, 102.0, tmp_path / "out.wav"))
    frame = lambda t: 2 * int(t * 48000)  # noqa: E731 - sample index of the left channel at t seconds in
    assert len(s) == frame(1.75)
    assert s[0] == 1000 and s[frame(0.24)] == 1000  # the tail of the first tone
    assert s[frame(0.3)] == 0 and s[frame(1.2)] == 0  # silence filled by wall clock
    assert s[frame(1.3)] == 1000 and s[-1] == 1000  # the second tone lands at 101.5 s
    track.close()


def test_audio_from_other_device_formats_is_converted(tmp_path):
    track = AudioTrack("mic", tmp_path, rate=24_000, channels=1)  # e.g. a Bluetooth headset mic
    ramp = b"".join(v.to_bytes(2, "little", signed=True) for v in range(0, 24_000))  # 1 s, distinct values
    track.write(11.0, ramp)
    s = _samples(track.extract(10.0, 11.0, tmp_path / "m.wav"))
    assert len(s) == 2 * 48000  # 1 s at 48 kHz stereo
    assert s[0] == s[1] == 0 and s[2 * 48000 - 2] == s[2 * 48000 - 1] == 23_999  # mono copied to both ears
    assert s[2 * 24000] == 12_000  # half-way through is half-way up the ramp: timing survives resampling
    track.close()


def test_audio_track_rotates_and_prunes_files(tmp_path):
    track = AudioTrack("mic", tmp_path, rate=100, channels=1)
    track.FILE_S = 1.0
    for i in range(5):
        track.write(10.0 + (i + 1) * 0.5, b"\x01\x00" * 50)
    track.flush()
    assert len(track.files()) >= 2
    track.prune(older_than=12.2)
    assert track.files()[0][0] >= 10.9
    track.close()


# ── clips (real FFmpeg) ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "Dota 2" / "sample.mp4"
    path.parent.mkdir(parents=True)
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_clip_store_indexes_edits_and_exports(tmp_path, sample_video):
    store = ClipStore(tmp_path / "clips.db", tmp_path / "thumbs")
    clip = store.add(sample_video, kind="clip", game_id="steam:570", game_name="Dota 2")
    assert clip["duration"] == pytest.approx(4, abs=0.1) and (clip["width"], clip["height"]) == (320, 180)
    assert clip["thumb"] and Path(clip["thumb"]).exists()

    store.update(clip["id"], title="  Rampage  ", favorite=True)
    assert store.list(favorites=True)[0]["title"] == "Rampage"
    assert store.list(query="ramp") and not store.list(query="nothing")

    trimmed = store.trim(clip["id"], 1.0, 3.0, encoder="x264")
    assert trimmed["duration"] == pytest.approx(2, abs=0.15) and trimmed["parent_id"] == clip["id"]
    gif = store.export_gif(clip["id"], 0, 1.5, width=160, fps=10)
    assert gif["path"].endswith(".gif") and gif["kind"] == "export"
    small = store.export_compact(clip["id"], target_mb=0.3)
    assert Path(small["path"]).stat().st_size < 0.45 * 1024 * 1024
    assert media_info(Path(small["path"]))["has_audio"]

    assert store.stats()["count"] == 4
    store.delete(trimmed["id"], delete_file=False)
    assert store.stats()["count"] == 3


def test_clip_store_imports_existing_files(tmp_path, sample_video):
    root = tmp_path / "Clutch"
    (root / "Valorant").mkdir(parents=True)
    copy = root / "Valorant" / "old clip.mp4"
    copy.write_bytes(sample_video.read_bytes())
    store = ClipStore(tmp_path / "c.db", tmp_path / "t")
    assert store.import_folder(root) == 1 and store.import_folder(root) == 0
    assert store.list()[0]["game_name"] == "Valorant"
    copy.unlink()
    assert store.prune_missing() == 1


def test_new_clip_paths_are_safe_and_unique(tmp_path):
    first = ClipStore.new_path(tmp_path, 'Bad: "Name"?', "clip")
    first.parent.mkdir(parents=True)
    first.touch()
    second = ClipStore.new_path(tmp_path, 'Bad: "Name"?', "clip")
    assert first.parent.name == "Bad Name" and second != first


# ── settings & API guard ─────────────────────────────────────────────────────


def test_settings_validation_and_persistence(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    seen = []
    store.on_change(seen.append)
    store.update({"buffer_seconds": 120, "hotkey_clip": "Alt+F8"})
    assert SettingsStore(tmp_path / "s.json").get().buffer_seconds == 120 and seen
    for bad in ({"buffer_seconds": 5}, {"quality": "insane"}, {"auto_buffer": "yes"}, {"nope": 1}):
        with pytest.raises(ValueError):
            store.update(bad)


@pytest.fixture
def desktop_api(tmp_path, monkeypatch, sample_video):
    from clutch.app import create_app
    from clutch.games import default_providers
    from clutch.local.desktop import Desktop
    from clutch.service import Clutch
    from clutch.store import Store

    monkeypatch.setenv("CLUTCH_TOKEN", "secret")
    svc = Clutch(Store(":memory:"), default_providers())
    desktop = Desktop(svc, home=tmp_path / "home", start_threads=False)
    desktop.settings.update({"clips_dir": str(tmp_path / "clips")})
    game_dir = tmp_path / "games" / "dota"
    game_dir.mkdir(parents=True)
    desktop.library.scanners = (
        lambda: [lib.Game("steam:570", "Dota 2", "steam", str(game_dir), {"uri": "steam://rungameid/570"}, steam_appid=570)],
    )
    desktop.library.scan()
    desktop.clips.add(sample_video, kind="clip", game_id="steam:570", game_name="Dota 2")
    return TestClient(create_app(svc, static_dir=None, desktop=desktop)), desktop


def test_desktop_api_requires_the_token(desktop_api):
    api, _ = desktop_api
    assert api.get("/api/desktop/status").status_code == 401
    assert api.get("/api/desktop/status", headers={"X-Clutch-Token": "wrong"}).status_code == 401
    assert api.get("/api/desktop/status", params={"token": "secret"}).status_code == 200
    assert api.get("/api/health").json()["desktop"] is True


def test_desktop_api_library_clips_and_settings(desktop_api):
    api, desktop = desktop_api
    h = {"X-Clutch-Token": "secret"}
    [game] = api.get("/api/desktop/library", headers=h).json()
    assert game["id"] == "steam:570" and game["stats_game"] == "dota2" and game["clips"] == 1
    assert game["art"]["cover"].endswith("/570/library_600x900_2x.jpg")
    assert api.post("/api/desktop/library/nope/launch", headers=h).status_code == 404

    clips = api.get("/api/desktop/clips", headers=h).json()
    clip_id = clips["items"][0]["id"]
    video = api.get(f"/api/desktop/clips/{clip_id}/file", params={"token": "secret"}, headers={"Range": "bytes=0-99"})
    assert video.status_code == 206 and len(video.content) == 100  # seekable
    assert api.patch(f"/api/desktop/clips/{clip_id}", headers=h, json={"favorite": True}).json()["favorite"] is True

    assert api.put("/api/desktop/settings", headers=h, json={"buffer_seconds": 3}).status_code == 400
    assert api.put("/api/desktop/settings", headers=h, json={"buffer_seconds": 90}).json()["buffer_seconds"] == 90
    assert api.get("/api/desktop/playtime", headers=h).json()["games"] == []


def test_desktop_routes_absent_in_web_mode(monkeypatch):
    from clutch.app import create_app
    from clutch.games import default_providers
    from clutch.service import Clutch
    from clutch.store import Store

    monkeypatch.delenv("CLUTCH_DESKTOP", raising=False)
    api = TestClient(create_app(Clutch(Store(":memory:"), default_providers()), static_dir=None))
    assert api.get("/api/desktop/status").status_code == 404


def test_stale_recorders_from_a_crashed_run_are_stopped(tmp_path):
    import time

    from clutch.local.capture import kill_stale_recorders

    spool = tmp_path / "spool"
    (spool / "buffer_1").mkdir(parents=True)
    # An FFmpeg left recording into our spool (what a crashed backend leaves behind)...
    stray = subprocess.Popen(
        [
            ffmpeg_exe(),
            "-v",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=5",
            "-t",
            "60",
            "-y",
            str(spool / "buffer_1" / "v.ts"),
        ],
        stdin=subprocess.DEVNULL,
    )
    # ...and one that belongs to someone else.
    other = subprocess.Popen(
        [
            ffmpeg_exe(),
            "-v",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:rate=5",
            "-t",
            "60",
            "-y",
            str(tmp_path / "other.ts"),
        ],
        stdin=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        assert kill_stale_recorders(spool) == 1
        stray.wait(timeout=5)
        assert other.poll() is None  # untouched
    finally:
        for p in (stray, other):
            if p.poll() is None:
                p.kill()


@pytest.mark.parametrize(("click_at", "clip_start"), [(3.0, 1.0), (3.0, 1.37), (5.2, 0.81), (4.4, 0.013)])
def test_live_encoded_audio_stays_in_sync(tmp_path, click_at, clip_start):
    """Stream-copied AAC must put a sound where it happened, relative to the clip's start."""
    import math

    track = AudioTrack("system", tmp_path, live_kbps=192)
    t0 = 1000.0
    tone = b"".join(int(12000 * math.sin(2 * math.pi * 1000 * i / 48000)).to_bytes(2, "little", signed=True) * 2 for i in range(960))
    t = t0
    for _ in range(400):  # 8 s of 20 ms chunks, with a 100 ms tone at click_at
        t += 0.02
        track.write(t, tone if t0 + click_at <= t - 0.02 < t0 + click_at + 0.1 else b"\x00" * 960 * 4)
    track.encoder.proc.stdin.close()
    track.encoder.proc.wait()

    files, shift = track.live_window(t0 + clip_start)
    listing = tmp_path / "a.txt"
    listing.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:r=30:d=8",
         "-itsoffset", f"{shift:.6f}", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "copy", "-bsf:a", "aac_adtstoasc", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(out), "-vn", "-ac", "1", str(tmp_path / "a.wav")], check=True)
    with wave.open(str(tmp_path / "a.wav")) as w:
        samples = memoryview(w.readframes(w.getnframes())).cast("h")
    heard = next(i for i, v in enumerate(samples) if abs(v) > 3000) / 48000
    assert abs(heard - (click_at - clip_start)) < 0.02  # within one AAC frame; measured -1.3 ms
    track.close()


def test_live_window_refuses_what_it_cannot_cover(tmp_path):
    track = AudioTrack("system", tmp_path, live_kbps=128)
    assert track.live_window(5.0) is None  # nothing encoded yet
    track.write(100.02, b"\x00" * 960 * 4)
    assert track.live_window(50.0) is None  # the encoder started after the clip would begin
    track.close()
