import pytest

from rlstats.parse import ParseError, PlayerRef, parse_replay
from rlstats.ranks import rank_label, rank_value, tier_name


def test_parses_player_by_platform_id(replay):
    m = parse_replay(replay, PlayerRef.parse("steam:111"))
    assert m is not None
    assert (m.team, m.won, m.team_goals, m.opponent_goals) == ("blue", True, 3, 1)
    assert (m.goals, m.shots, m.shooting_pct) == (2, 3, pytest.approx(66.67, abs=0.01))
    assert [t.name for t in m.teammates] == ["Buddy"]
    assert m.teammates[0].key == "epic:222"
    assert m.opponents == ["Opp1", "Opp2"]
    assert m.rank_value == 13.25
    assert m.link == "https://ballchasing.com/replay/abc-123"


def test_platform_id_beats_a_duplicate_name(replay):
    replay["orange"]["players"][0]["name"] = "Me"  # an opponent with the same display name
    m = parse_replay(replay, PlayerRef.parse("steam:111", "Me"))
    assert m.team == "blue"


def test_name_fallback_is_case_insensitive(replay):
    m = parse_replay(replay, PlayerRef.parse(None, "  me "))
    assert m is not None and m.team == "blue"


def test_platform_must_match_when_given(replay):
    assert parse_replay(replay, PlayerRef.parse("epic:111")) is None


def test_player_not_in_replay_returns_none(replay):
    assert parse_replay(replay, PlayerRef.parse("steam:999")) is None


def test_lobby_average_excludes_the_player(replay):
    m = parse_replay(replay, PlayerRef.parse("steam:111"))
    assert m.lobby["saves"] == pytest.approx((1 + 4 + 1) / 3)
    assert m.lobby["goals"] == pytest.approx(1.0)


def test_team_goals_fall_back_to_player_sum(replay):
    del replay["blue"]["stats"]
    del replay["orange"]["stats"]
    m = parse_replay(replay, PlayerRef.parse("steam:111"))
    assert (m.team_goals, m.opponent_goals) == (3, 2)


def test_pending_replay_raises(replay):
    replay["status"] = "pending"
    with pytest.raises(ParseError):
        parse_replay(replay, PlayerRef.parse("steam:111"))


def test_missing_stat_blocks_do_not_crash(replay):
    for team in ("blue", "orange"):
        for p in replay[team]["players"]:
            p["stats"] = {"core": {"goals": 0}}
    m = parse_replay(replay, PlayerRef.parse("steam:111"))
    assert m.bpm is None and m.pct_behind_ball is None and m.shooting_pct == 0


def test_rank_helpers():
    assert tier_name(0) == "Unranked"
    assert tier_name(13) == "Diamond I"
    assert tier_name(21) == "Grand Champion III"
    assert tier_name(22) == "Supersonic Legend"
    assert rank_value(13, 4) == 13.75
    assert rank_value(None, 1) is None
    assert rank_label(13.75) == "Diamond I Div 4"
