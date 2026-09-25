from dataclasses import replace

import pytest

from rlstats import analytics as A
from rlstats.parse import PlayerRef, Teammate, parse_replay


@pytest.fixture
def base(replay):
    return parse_replay(replay, PlayerRef.parse("steam:111"))


def seq(base, results, *, start_minute=0, gap_minutes=8, **overrides):
    """Matches with the given W/L results, `gap_minutes` apart."""
    out = []
    for i, won in enumerate(results):
        minute = start_minute + i * gap_minutes
        date = f"2026-09-01T{18 + minute // 60:02d}:{minute % 60:02d}:00Z"
        out.append(replace(base, id=f"m{minute}", date=date, won=won, **overrides))
    return out


def test_streaks(base):
    ms = seq(base, [True, True, True, False, True, True])
    s = A.streaks(ms)
    assert s["current"] == {"type": "win", "length": 2}
    assert (s["longest_win"], s["longest_loss"]) == (3, 1)


def test_streaks_empty():
    assert A.streaks([])["current"] == {"type": None, "length": 0}


def test_summary(base):
    ms = seq(base, [True, False, True, True])
    s = A.summarize(ms)
    assert (s["games"], s["wins"], s["losses"], s["win_rate"]) == (4, 3, 1, 75.0)
    assert s["shooting_pct"] == pytest.approx(66.7, abs=0.1)
    assert s["avg"]["goals"] == 2


def test_rolling_uses_partial_windows(base):
    ms = seq(base, [True, False, False, True])
    assert A.rolling(ms, lambda m: 100.0 if m.won else 0.0, window=2) == [100.0, 50.0, 0.0, 50.0]


def test_sessions_split_on_gap(base):
    first = seq(base, [True] * 3, start_minute=0)
    second = seq(base, [False] * 2, start_minute=240)  # 4h later
    sessions = A.split_sessions(first + second)
    assert [len(s) for s in sessions] == [3, 2]
    curve = A.session_analysis(first + second)["tilt_curve"]
    assert curve[0] == {"game": "1", "games": 2, "win_rate": 50.0}


def test_session_rank_change_is_per_playlist(base):
    doubles = seq(base, [True, True], rank_tier=13, rank_division=1)
    doubles[1] = replace(doubles[1], rank_division=3)
    duels = [replace(m, playlist_id="ranked-duels", rank_tier=9, rank_division=1, id=m.id + "d") for m in doubles]
    rows = A.session_analysis(doubles + duels)["recent"]
    # Doubles moved +2 divisions (0.5); duels didn't move. Cross-playlist ranks are never compared.
    assert rows[0]["rank_change"] == 0.5


def test_teammates_min_games(base):
    mate = [Teammate("epic:222", "Buddy")]
    ms = seq(base, [True, True, False], teammates=mate) + seq(
        base, [True], start_minute=100, teammates=[Teammate("x:1", "Rando")]
    )
    rows = A.teammates(ms, min_games=3)
    assert [(r["name"], r["games"], r["win_rate"]) for r in rows] == [("Buddy", 3, 66.7)]


def test_compare_windows_requires_enough_games(base):
    assert A.compare_windows(seq(base, [True] * 8)) is None
    cmp = A.compare_windows(seq(base, [False] * 10 + [True] * 10), window=10)
    assert cmp["stats"]["win_rate"] == {"recent": 100.0, "previous": 0.0, "delta": 100.0}


def test_rank_history_keeps_changes_and_latest(base):
    ms = seq(base, [True] * 4, rank_tier=13, rank_division=1)
    ms[2] = replace(ms[2], rank_division=2)
    ms[3] = replace(ms[3], rank_division=2)
    hist = A.rank_history(ms)[0]
    assert [p["value"] for p in hist["points"]] == [13.0, 13.25, 13.25]
    assert hist["peak"] == "Diamond I Div 2"
    assert hist["change"] == 0.25


def test_playstyle_index_vs_lobby(base):
    p = {a["stat"]: a for a in A.playstyle([base])}
    assert p["goals"]["index"] == 200  # 2 goals vs lobby average of 1
    assert p["saves"]["index"] == pytest.approx(50, abs=1)


def test_win_factors_detect_the_signal(demo_matches):
    factors = {f["stat"]: f for f in A.win_factors(demo_matches)}
    # The demo generator makes boost starvation lose games and good positioning win them.
    assert factors["pct_zero_boost"]["difference"] < -5
    assert factors["pct_behind_ball"]["difference"] > 5


def test_insights_are_guarded_by_sample_size(base):
    only = A.insights(seq(base, [True] * 5))
    assert len(only) == 1 and "10+ games" in only[0]["text"]


def test_insights_on_a_season(demo_matches):
    items = A.insights(demo_matches)
    assert 1 <= len(items) <= 7
    assert all(i["tone"] in {"positive", "negative", "tip", "neutral"} and i["text"] for i in items)


def test_report_views_and_log(demo_matches):
    report = A.build_report(demo_matches, "SkyReach")
    assert report["views"]["all"]["summary"]["games"] == len(demo_matches)
    assert {p["id"] for p in report["playlists"]} <= set(report["views"])
    assert len(report["matches"]) == len(demo_matches)
    assert report["matches"][0]["date"] >= report["matches"][-1]["date"]  # newest first
    trend = report["views"]["all"]["trend"]
    assert len(trend["win_rate"]) == len(trend["dates"]) == len(demo_matches)


def test_report_handles_no_matches():
    report = A.build_report([], "Nobody")
    assert report["views"]["all"]["summary"]["games"] == 0
    assert report["matches"] == []
