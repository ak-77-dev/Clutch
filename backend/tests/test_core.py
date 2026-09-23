from dataclasses import replace
from typing import Any

import pytest
import requests

from clutch import analytics as A
from clutch.games.base import GameProvider
from clutch.games.league import META as LOL
from clutch.http import ApiError, AuthFailed, JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile
from clutch.service import Clutch, NotConfigured
from clutch.store import Store

# ── http ─────────────────────────────────────────────────────────────────────


def test_sliding_window_enforces_every_window():
    now = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(round(s, 3))
        now[0] += s

    lim = SlidingWindowLimiter([(2, 1.0), (3, 10.0)], clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        lim.acquire()
    # 3rd call waits for the 1s window, 4th for the 10s window.
    assert slept == [1.0, 9.0]


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}

    def json(self):
        return self._b


class Session:
    def __init__(self, seq):
        self.seq, self.headers = list(seq), {}

    def get(self, url, params=None, timeout=None):
        r = self.seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(seq, sleeps):
    return JsonClient(session=Session(seq), sleep=sleeps.append)


def test_json_client_retries_and_maps_errors():
    sleeps: list[float] = []
    assert client([Resp(429, headers={"Retry-After": "2"}), Resp(200, {"ok": 1})], sleeps).get("u") == {"ok": 1}
    assert sleeps == [2.0]
    sleeps.clear()  # HenrikDev signals its window with x-ratelimit-reset instead of Retry-After
    assert client([Resp(429, headers={"x-ratelimit-reset": "37"}), Resp(200, {"ok": 1})], sleeps).get("u") == {"ok": 1}
    assert sleeps == [37.0]
    with pytest.raises(NotFound):
        client([Resp(404)], []).get("u")
    with pytest.raises(AuthFailed):
        client([Resp(403)], []).get("u")
    with pytest.raises(ApiError):
        client([requests.ConnectionError()] * 4, []).get("u")


# ── analytics ────────────────────────────────────────────────────────────────

META = GameMeta(
    id="t",
    name="T",
    character_label="Hero",
    search_hint="",
    accent="#fff",
    metrics=(Metric("a", "Alpha", "float1"), Metric("d", "Deaths", "float1", higher_is_better=False)),
    kpis=("a",),
    trend_metrics=("a",),
    factor_metrics=("a", "d"),
    card_metrics=("a",),
)


def mk(i: int, won: bool, *, minute: int | None = None, a: float = 1.0, d: float = 1.0, char="X", result=None, **kw) -> Match:
    minute = i * 30 if minute is None else minute
    date = f"2026-09-01T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"
    return Match(
        game="t",
        id=str(i),
        date=date,
        mode="Ranked",
        duration_s=600,
        result=result or ("win" if won else "loss"),
        character=char,
        character_icon=None,
        metrics={"a": a, "d": d},
        **kw,
    )


def test_remakes_are_excluded():
    ms = [mk(0, True), mk(1, False, result="remake"), mk(2, False)]
    s = A.summarize(META, A.counted(ms))
    assert (s["games"], s["wins"], s["losses"], s["win_rate"]) == (2, 1, 1, 50.0)


def test_streaks_and_sessions():
    ms = A.counted([mk(i, w, minute=i * 12) for i, w in enumerate([True, True, False, True, True, True])] + [mk(9, False, minute=600)])
    assert A.streaks(ms)["current"] == {"type": "loss", "length": 1}
    assert A.streaks(ms)["longest_win"] == 3
    assert [len(s) for s in A.split_sessions(ms)] == [6, 1]


def test_win_factors_and_insight_phrasing():
    ms = [mk(i, won=i % 2 == 0, a=10 if i % 2 == 0 else 2, d=1 if i % 2 == 0 else 6) for i in range(20)]
    f = {x["metric"]: x for x in A.win_factors(META, ms)}
    assert f["a"]["difference"] == 100.0 and f["d"]["difference"] == -100.0
    text = " ".join(i["text"] for i in A.insights(META, ms))
    assert "alpha > 6.0" in text and "deaths ≤ 3.5" in text  # split at the median


def test_insights_need_ten_games():
    assert "10+" in A.insights(META, [mk(i, True) for i in range(5)])[0]["text"]


def test_character_pool_and_teammates():
    ms = [mk(i, i < 3, char="A" if i < 4 else "B", teammates=["Duo"], team_keys=["k1"]) for i in range(6)]
    pool = A.character_pool(META, ms)
    assert pool[0]["name"] == "A" and pool[0]["games"] == 4 and pool[0]["win_rate"] == 75.0
    assert A.teammates(ms)[0] == {"key": "k1", "name": "Duo", "games": 6, "wins": 3, "win_rate": 50.0}


def test_rank_history_only_changes():
    ms = [mk(i, True, rank_value=v, rank_label=str(v)) for i, v in enumerate([10, 10, 11, 11, 10])]
    assert [p["value"] for p in A.rank_history(ms)] == [10, 11, 10]


def test_overview_shape_for_real_game_meta():
    ms = [replace(mk(i, i % 3 != 0), metrics={k.key: float(i % 7) for k in LOL.metrics}) for i in range(30)]
    ov = A.build_overview(LOL, ms)
    assert set(ov["trend"]) >= {"dates", "win_rate", *LOL.trend_metrics}
    assert len(ov["trend"]["dates"]) == 30


# ── service (fake provider) ─────────────────────────────────────────────────


class FakeProvider(GameProvider):
    meta = META

    def __init__(self, configured=True):
        self._configured = configured
        self.remote = [{"id": f"m{i}", "date": f"2026-09-0{i + 1}T10:00:00Z", "won": i % 2 == 0} for i in range(3)]
        self.fetched: list[str] = []

    def configured(self):
        return self._configured

    def resolve(self, query):
        if query == "ghost#1":
            raise NotFound("not found")
        return Profile(game="t", key="p1", name=query)

    def refresh_profile(self, profile):
        profile.level = 42
        return profile

    def list_match_ids(self, profile, limit):
        return [m["id"] for m in reversed(self.remote)][:limit]

    def fetch_match(self, profile, match_id):
        self.fetched.append(match_id)
        return next(m for m in self.remote if m["id"] == match_id)

    def match_id_of(self, raw):
        return raw["id"]

    def match_date(self, raw):
        return raw["date"]

    def parse(self, raw: dict[str, Any], profile):
        return mk(int(raw["id"][1:]), raw["won"])

    def demo_profile(self):
        return Profile(game="t", key="demo-t", name="Demo", demo=True)

    def demo_matches(self):
        return [{"id": "d1", "date": "2026-01-01T00:00:00Z", "won": True}]


def test_lookup_syncs_once_then_incrementally():
    p = FakeProvider()
    svc = Clutch(Store(":memory:"), [p])
    prof = svc.lookup("t", "Player#1")
    assert p.fetched == ["m2", "m1", "m0"] and svc.store.profile("t", "p1").level == 42
    assert svc.lookup("t", "player#1").key == "p1"  # cached alias, case-insensitive
    p.remote.append({"id": "m3", "date": "2026-09-09T10:00:00Z", "won": True})
    assert svc.sync("t", prof.key)["new"] == 1 and p.fetched[-1] == "m3"
    assert svc.match_page("t", "p1")["total"] == 4


def test_unconfigured_and_not_found():
    svc = Clutch(Store(":memory:"), [FakeProvider(configured=False)])
    with pytest.raises(NotConfigured):
        svc.lookup("t", "Player#1")
    svc2 = Clutch(Store(":memory:"), [FakeProvider()])
    with pytest.raises(NotFound):
        svc2.lookup("t", "ghost#1")


def test_demo_profile_needs_no_key():
    svc = Clutch(Store(":memory:"), [FakeProvider(configured=False)])
    prof = svc.lookup("t", "demo")
    assert prof.demo and svc.match_page("t", prof.key)["total"] == 1
    assert svc.sync("t", prof.key)["demo"] is True


def test_store_links_one_match_to_many_players(tmp_path):
    st = Store(tmp_path / "c.db")
    st.add_matches("g", "a", [("m1", "2026-01-01", {"x": 1})])
    st.add_matches("g", "b", [("m1", "2026-01-01", {"x": 2})])
    assert st.raw_matches("g", "a") == [{"x": 2}] and st.known_match_ids("g", "b") == {"m1"}
