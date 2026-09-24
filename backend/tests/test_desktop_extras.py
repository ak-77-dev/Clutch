"""Storage cleanup, report cards, goals, friends, auto-clip, editing, sharing and clip<->match links."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from clutch.local import autoclip, edit, share
from clutch.local.capture import ffmpeg_exe, media_info
from clutch.local.goals import GoalStore, day_start, evaluate, week_start
from clutch.local.reports import ReportStore, match_window, summarize_matches
from clutch.local.storage import plan_cleanup

# ── storage ─────────────────────────────────────────────────────────────────


def _clip(i, age_days, mb, fav=False, now=1_000_000_000.0):
    return {"id": i, "created_at": now - age_days * 86400, "size": int(mb * 1024**2), "favorite": fav, "exists": True}


def test_storage_plan_by_age_and_size_keeps_favorites():
    now = 1_000_000_000.0
    clips = [_clip(1, 40, 500, now=now), _clip(2, 35, 500, fav=True, now=now), _clip(3, 5, 800, now=now), _clip(4, 1, 800, now=now)]
    by_age = plan_cleanup(clips, max_days=30, now=now)
    assert by_age.clip_ids == [1]  # the old favorite stays
    by_size = plan_cleanup(clips, max_gb=1.5, now=now)  # 2.6 GB total -> drop oldest non-favorites until under
    assert by_size.clip_ids == [1, 3] and by_size.bytes == int(1300 * 1024**2)
    assert plan_cleanup(clips, now=now).clip_ids == []  # both rules off


# ── report cards ────────────────────────────────────────────────────────────


def _match(i, date, result, kda):
    return SimpleNamespace(
        id=str(i), date=date, result=result, metrics={"kda": kda}, character="Ahri", score_line="10–5", rank_label="Gold II"
    )


META = SimpleNamespace(kpis=("kda",), metric=lambda k: SimpleNamespace(label="KDA", fmt="float2", higher_is_better=True))


def test_report_card_summarises_the_session(tmp_path):
    session_start = datetime(2026, 9, 1, 18, 0).astimezone().timestamp()
    iso = lambda offset: datetime.fromtimestamp(session_start + offset).astimezone().isoformat()  # noqa: E731
    played = [_match(1, iso(60), "win", 4.0), _match(2, iso(2000), "loss", 1.5), _match(3, iso(4000), "win", 6.5)]
    old = [_match(9, iso(-86400), "loss", 2.0)]
    in_window = match_window(played + old, session_start, session_start + 5000)
    assert [m.id for m in in_window] == ["1", "2", "3"]
    summary = summarize_matches(in_window, META, old)
    assert (summary["games"], summary["wins"], summary["losses"]) == (3, 2, 1)
    assert summary["best"]["id"] == "3" and summary["metric"]["usual"] == 2.0

    store = ReportStore(tmp_path / "r.db")
    report = store.create(
        {"id": 7, "game_id": "riot:league_of_legends", "game_name": "League", "started_at": 1, "ended_at": 2, "seconds": 3600}, [4, 5]
    )
    assert report["clips"] == [4, 5] and report["stats"] is None
    filled = store.attach_stats(report["id"], "lol", summary)
    assert filled["stats"]["wins"] == 2 and store.recent()[0]["id"] == report["id"]


# ── goals ───────────────────────────────────────────────────────────────────


def test_goals_track_time_clips_win_rate_and_rank(tmp_path):
    store = GoalStore(tmp_path / "g.db")
    with pytest.raises(ValueError):
        store.add("rank", 20)  # needs a game
    with pytest.raises(ValueError):
        store.add("win_rate", 120, "valorant")
    now = time.time()
    today = datetime.fromtimestamp(day_start(now)).date().isoformat()
    ctx = {
        "now": now,
        "daily": [{"date": today, "seconds": 3 * 3600, "games": {"steam:570": 3 * 3600}}],
        "clips": [{"created_at": week_start(now) + 10, "game_id": "steam:570"}] * 4,
        "profiles": {"valorant": {"rank_value": 14, "rank_label": "Gold 3", "recent": ["win"] * 12 + ["loss"] * 8}},
    }
    cap = evaluate(store.add("daily_cap", 2.5), ctx)
    assert cap["state"] == "over" and cap["value"] == 3.0
    assert evaluate(store.add("weekly_hours", 10, "steam:570"), ctx)["progress"] == pytest.approx(0.3)
    assert evaluate(store.add("clips", 3), ctx)["state"] == "done"
    assert evaluate(store.add("win_rate", 55, "valorant"), ctx)["state"] == "done"  # 60% over 20 games
    rank = evaluate(store.add("rank", 17, "valorant", "Platinum 3"), ctx)
    assert rank["state"] == "ok" and rank["current"] == "Gold 3"
    assert evaluate({"kind": "rank", "target": 5, "game_id": "lol"}, ctx)["state"] == "unlinked"
    assert len(store.all()) == 5


# ── friends ─────────────────────────────────────────────────────────────────


def test_friends_feed_from_public_profiles(tmp_path):
    from clutch.games import default_providers
    from clutch.local.friends import FriendStore, feed
    from clutch.service import Clutch
    from clutch.store import Store

    svc = Clutch(Store(":memory:"), default_providers())
    friends = FriendStore(tmp_path / "f.json")
    for game in ("valorant", "dota2"):
        p = svc.lookup(game, "demo")
        friends.add(game, p.key, p.name)
    friends.add("valorant", svc.lookup("valorant", "demo").key, "dupe")  # adding twice is a no-op
    assert len(FriendStore(tmp_path / "f.json").friends) == 2
    out = feed(friends.friends, svc)
    assert {c["game"] for c in out["cards"]} == {"valorant", "dota2"} and out["cards"][0]["rank"]
    assert len(out["items"]) == 10 and out["items"] == sorted(out["items"], key=lambda m: m["date"], reverse=True)
    friends.remove("dota2", svc.lookup("dota2", "demo").key)
    assert [f["game"] for f in friends.friends] == ["valorant"]


# ── auto-clip ───────────────────────────────────────────────────────────────


def test_league_events_for_the_active_player_only():
    events = [
        {"EventID": 1, "EventName": "ChampionKill", "KillerName": "Nightfall"},
        {"EventID": 2, "EventName": "ChampionKill", "KillerName": "Someone Else"},
        {"EventID": 3, "EventName": "Multikill", "KillerName": "Nightfall", "KillStreak": 3},
        {"EventID": 4, "EventName": "Multikill", "KillerName": "nightfall", "KillStreak": 5},
        {"EventID": 5, "EventName": "Ace", "Acer": "Nightfall", "AcingTeam": "ORDER"},
    ]
    seen: set[int] = set()
    got = autoclip.league_highlights(events, "Nightfall#NA1", seen)
    assert [(h.level, h.title) for h in got] == [("kill", "Kill"), ("multikill", "Triple kill"), ("ace", "Penta kill"), ("ace", "Ace")]
    assert autoclip.league_highlights(events, "Nightfall#NA1", seen) == []  # already seen


def test_dota_and_cs_trackers():
    dota = autoclip.DotaTracker()
    payload = lambda k: {"map": {"matchid": "1"}, "player": {"kills": k}}  # noqa: E731
    assert dota.update(payload(0), now=0) == []  # first sight of a match just sets the baseline
    assert dota.update(payload(1), now=10)[0].level == "kill"
    assert dota.update(payload(2), now=15)[0].title == "Double kill"
    assert dota.update(payload(4), now=20)[0].title == "Ultra kill"
    assert dota.update(payload(5), now=60)[0].level == "kill"  # outside the 18 s window: a fresh streak

    cs = autoclip.CsTracker()

    def mk(kills, rnd=3, who="me"):
        return {"provider": {"steamid": "me"}, "map": {"round": rnd}, "player": {"steamid": who, "state": {"round_kills": kills}}}

    assert cs.update(mk(1))[0].level == "kill"
    assert cs.update(mk(3))[0].title == "3K"
    assert cs.update(mk(5, who="teammate")) == []  # spectating someone else
    assert cs.update(mk(5))[0].title == "Ace"
    assert cs.update(mk(1, rnd=4))[0].title == "Kill"  # new round resets


def test_autoclipper_merges_bursts_and_respects_the_minimum(monkeypatch):
    monkeypatch.setattr(autoclip, "POST_ROLL_S", 0.05)
    saved = []
    level = {"v": "multikill"}
    clipper = autoclip.AutoClipper(lambda title, s: saved.append((title, s)), lambda: level["v"], lambda: 60.0)
    assert clipper.offer(autoclip.Highlight("steam:570", "kill", "Kill")) is False  # below the minimum
    clipper.offer(autoclip.Highlight("steam:570", "multikill", "Double kill"))
    clipper.offer(autoclip.Highlight("steam:570", "ace", "Rampage"))
    time.sleep(0.3)
    assert saved == [("Rampage", 20.0)]  # one clip for the burst, named after the biggest moment
    level["v"] = "kill"
    clipper.offer(autoclip.Highlight("steam:570", "kill", "Kill"))
    clipper.flush()
    assert saved[-1][0] == "Kill"


def test_gsi_config_install_and_auth(tmp_path):
    install = tmp_path / "dota 2 beta"
    (install / "game" / "dota" / "cfg").mkdir(parents=True)
    path = autoclip.install_gsi("steam:570", str(install), "tok123")
    text = path.read_text(encoding="utf-8")
    assert f"127.0.0.1:{autoclip.GSI_PORT}/gsi/dota" in text and '"token" "tok123"' in text
    assert autoclip.gsi_installed("steam:570", str(install))
    with pytest.raises(ValueError):
        autoclip.install_gsi("steam:730", str(tmp_path / "nope"), "t")

    got = []
    server = autoclip.GsiServer("tok123", got.append, port=0)
    good = {"auth": {"token": "tok123"}, "map": {"matchid": "9"}, "player": {"kills": 0}}
    server.handle("dota", good)
    assert server.handle("dota", {**good, "player": {"kills": 1}, "auth": {"token": "wrong"}}) == []  # forged
    assert server.handle("dota", {**good, "player": {"kills": 1}})[0].level == "kill"
    assert len(got) == 1


# ── editing (real FFmpeg) ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("edit")

    def make(name, seconds, tracks=1):
        path = d / name
        audio = []
        for i in range(tracks):
            audio += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * i}:duration={seconds}"]
        maps = ["-map", "0:v"] + [a for i in range(tracks) for a in ("-map", f"{i + 1}:a")]
        subprocess.run(
            [ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={seconds}", *audio, *maps,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
            check=True,
        )  # fmt: skip
        return path

    return {"a": make("a.mp4", 2), "b": make("b.mp4", 3), "tracks": make("t.mp4", 2, tracks=3)}


def test_montage_caption_vertical(tmp_path, clips):
    out = edit.montage(
        [{"path": str(clips["a"])}, {"path": str(clips["b"])}], tmp_path / "m.mp4", width=640, height=360, fps=30, encoder="x264"
    )
    info = media_info(out)
    assert info["duration"] == pytest.approx(5, abs=0.2) and (info["width"], info["height"]) == (640, 360) and info["has_audio"]
    with pytest.raises(ValueError):
        edit.montage([{"path": str(clips["a"])}], tmp_path / "x.mp4")

    cap = edit.caption(clips["a"], tmp_path / "c.mp4", 'GG: "clutch" 1v4', encoder="x264")
    assert media_info(cap)["duration"] == pytest.approx(2, abs=0.2)
    with pytest.raises(ValueError):
        edit.caption(clips["a"], tmp_path / "c2.mp4", "   ")

    for mode in ("blur", "crop"):
        v = media_info(edit.vertical(clips["b"], tmp_path / f"v_{mode}.mp4", mode=mode, encoder="x264"))
        assert (v["width"], v["height"]) == (1080, 1920)


def test_export_without_mic_keeps_the_game_track(tmp_path, clips):
    out = edit.without_mic(clips["tracks"], tmp_path / "nomic.mp4")
    assert edit.audio_track_count(out) == 1
    with pytest.raises(ValueError):
        edit.without_mic(clips["a"], tmp_path / "nope.mp4")


# ── sharing (no network) ────────────────────────────────────────────────────


class _FakeHttp:
    def __init__(self, status, text):
        self.status, self.text, self.calls = status, text, []

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data, files["fileToUpload"][0]))
        return SimpleNamespace(ok=self.status == 200, status_code=self.status, text=self.text)


def test_share_upload_and_errors(tmp_path, clips):
    http = _FakeHttp(200, "https://files.catbox.moe/abc123.mp4\n")
    assert share.upload(clips["a"], "catbox", session=http) == "https://files.catbox.moe/abc123.mp4"
    assert http.calls[0][1]["reqtype"] == "fileupload"
    litter = _FakeHttp(200, "https://litter.catbox.moe/x.mp4")
    share.upload(clips["a"], "litterbox", session=litter)
    assert litter.calls[0][1]["time"] == "72h"
    with pytest.raises(share.ShareError):
        share.upload(clips["a"], "catbox", session=_FakeHttp(412, "Nope"))
    big = tmp_path / "big.mp4"
    with open(big, "wb") as fh:
        fh.truncate(201 * 1024 * 1024)  # sparse: over catbox's limit without writing 201 MB
    with pytest.raises(share.ShareError, match="limit"):
        share.upload(big, "catbox", session=http)


# ── clips <-> matches, via the API ──────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch, clips):
    from clutch.app import create_app
    from clutch.games import default_providers
    from clutch.local import library as lib
    from clutch.local.desktop import Desktop
    from clutch.service import Clutch
    from clutch.store import Store

    monkeypatch.setenv("CLUTCH_TOKEN", "t")
    svc = Clutch(Store(":memory:"), default_providers())
    desk = Desktop(svc, home=tmp_path / "home", start_threads=False)
    desk.settings.update({"clips_dir": str(tmp_path / "clipsdir")})
    (tmp_path / "dota").mkdir()
    desk.library.scanners = (lambda: [lib.Game("steam:570", "Dota 2", "steam", str(tmp_path / "dota"), {"uri": "steam://rungameid/570"})],)
    desk.library.scan()
    profile = svc.lookup("dota2", "demo")
    desk.settings.update({"linked_profiles": {"dota2": profile.key}})
    return TestClient(create_app(svc, static_dir=None, desktop=desk)), desk, svc, profile


def test_clip_links_to_the_match_it_was_recorded_in(api, clips, tmp_path):
    client, desk, svc, profile = api
    h = {"X-Clutch-Token": "t"}
    match = svc.matches("dota2", profile.key)[5]
    end = datetime.fromisoformat(match.date.replace("Z", "+00:00")).timestamp() + 600  # ten minutes into that match
    path = tmp_path / "clip.mp4"
    path.write_bytes(clips["a"].read_bytes())
    os.utime(path, (end, end))
    clip = desk.clips.add(path, kind="clip", game_id="steam:570", game_name="Dota 2")
    got = client.get(f"/api/desktop/clips/{clip['id']}/match", headers=h).json()["match"]
    assert got["id"] == match.id and got["game"] == "dota2"
    assert client.get(f"/api/desktop/matches/dota2/{profile.key}/clips", headers=h).json() == {match.id: [clip["id"]]}
    assert client.get("/api/desktop/matches/dota2/someone-else/clips", headers=h).json() == {}


def test_goal_friend_report_and_session_routes(api):
    client, desk, svc, _ = api
    h = {"X-Clutch-Token": "t"}
    goal = client.post("/api/desktop/goals", headers=h, json={"kind": "daily_cap", "target": 3}).json()
    assert client.get("/api/desktop/goals", headers=h).json()[0]["progress"]["state"] == "ok"
    assert client.post("/api/desktop/goals", headers=h, json={"kind": "nonsense", "target": 1}).status_code == 400
    assert client.delete(f"/api/desktop/goals/{goal['id']}", headers=h).json() == {"ok": True}

    assert client.post("/api/desktop/friends", headers=h, json={"game": "valorant", "query": "demo"}).status_code == 200
    assert client.get("/api/desktop/friends", headers=h).json()["cards"][0]["game"] == "valorant"

    report = desk.reports.create({"game_id": "steam:570", "game_name": "Dota 2", "started_at": 0, "ended_at": 1, "seconds": 1}, [])
    assert client.get(f"/api/desktop/reports/{report['id']}", headers=h).json()["game_name"] == "Dota 2"
    assert client.get("/api/desktop/session", headers=h).json()["playing"] is None
    assert client.get("/api/desktop/storage", headers=h).json()["plan"]["count"] == 0
    assert [g["game_id"] for g in client.get("/api/desktop/autoclip", headers=h).json()] == ["riot:league_of_legends", "steam:570"]
