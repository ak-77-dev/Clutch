import copy

import pytest

from clutch.games.demo_data import LOL_DEMO_PROFILE, VAL_DEMO_PROFILE, lol_matches, val_matches
from clutch.games.league import LeagueProvider, rank_label, rank_value
from clutch.games.rocketleague import RocketLeagueProvider
from clutch.games.valorant import ValorantProvider, tier_name
from clutch.models import Profile


@pytest.fixture(scope="module")
def lol():
    p = LeagueProvider(api_key="")
    p.__dict__["patch"] = "16.18.1"  # no network in tests
    return p


def lol_profile():
    return Profile(**copy.deepcopy(LOL_DEMO_PROFILE))


def test_league_parse_metrics_are_consistent(lol):
    raw = lol_matches()[0]
    m = lol.parse(raw, lol_profile())
    me = next(p for p in raw["info"]["participants"] if p["puuid"] == LOL_DEMO_PROFILE["key"])
    minutes = raw["info"]["gameDuration"] / 60
    assert m.character == me["championName"]
    assert m.metrics["kda"] == round((me["kills"] + me["assists"]) / max(me["deaths"], 1), 2)
    cs = me["totalMinionsKilled"] + me["neutralMinionsKilled"]
    assert m.metrics["cs_per_min"] == pytest.approx(cs / minutes, abs=0.01)
    assert len(m.scoreboard) == 10 and sum(r.is_self for r in m.scoreboard) == 1
    assert m.character_icon.endswith(f"/champion/{me['championName']}.png")
    assert len(m.teammates) == 4 and len(m.team_keys) == 4


def test_league_remake_and_absent_player(lol):
    raw = lol_matches()[1]
    raw["info"]["gameDuration"] = 200
    assert lol.parse(raw, lol_profile()).result == "remake"
    stranger = Profile(game="lol", key="nobody", name="x")
    assert lol.parse(raw, stranger) is None


def test_league_millisecond_durations(lol):
    raw = lol_matches()[2]
    secs = raw["info"]["gameDuration"]
    raw["info"]["gameDuration"] = secs * 1000
    assert lol.parse(raw, lol_profile()).duration_s == pytest.approx(secs)


def test_league_rank_math():
    assert rank_value("GOLD", "IV", 0) < rank_value("GOLD", "I", 99) < rank_value("PLATINUM", "IV", 0)
    assert rank_value("MASTER", "I", 200) > rank_value("DIAMOND", "I", 100)
    assert rank_label("EMERALD", "III") == "Emerald III" and rank_label("CHALLENGER", "I") == "Challenger"
    assert rank_value(None, None) is None


def test_league_demo_ranked_record_matches_history(lol):
    prof = lol.demo_profile()
    solo = prof.ranks[0]
    games = [m for m in lol_matches() if m["info"]["queueId"] == 420]
    assert solo["wins"] + solo["losses"] == sum(
        1 for g in games for p in g["info"]["participants"] if p["puuid"] == prof.key and not p["gameEndedInEarlySurrender"]
    )


def test_league_demo_player_keeps_their_pick(lol):
    # Regression: the generator used to re-roll the player's own champion.
    counts: dict[str, int] = {}
    for raw in lol_matches():
        m = lol.parse(raw, lol_profile())
        if m.mode == "Ranked Solo/Duo" and m.role == "Mid":
            counts[m.character] = counts.get(m.character, 0) + 1
    assert set(counts) <= {"Ahri", "Viktor", "Syndra", "Orianna", "Yasuo", "Akali", "Sylas"}


def test_valorant_parse(tmp_path):
    v = ValorantProvider(api_key="")
    raw = val_matches()[0]
    prof = Profile(**copy.deepcopy(VAL_DEMO_PROFILE))
    m = v.parse(raw, prof)
    me = next(p for p in raw["players"]["all_players"] if p["puuid"] == prof.key)
    rounds = raw["metadata"]["rounds_played"]
    assert m.metrics["acs"] == pytest.approx(me["stats"]["score"] / rounds, abs=0.1)
    assert m.duration_s == pytest.approx(raw["metadata"]["game_length"] / 1000)  # ms payloads normalized
    assert m.map == raw["metadata"]["map"] and m.role
    assert len(m.scoreboard) == 10
    won, lost = (int(x) for x in m.score_line.split("–"))
    assert (m.result == "win") == (won > lost)


def test_valorant_draw_and_unrated_rank():
    v = ValorantProvider(api_key="")
    raw = val_matches()[3]
    prof = Profile(**copy.deepcopy(VAL_DEMO_PROFILE))
    for t in raw["teams"].values():
        t.update(has_won=False, rounds_won=12, rounds_lost=12)
    raw["metadata"]["mode"] = "Unrated"
    m = v.parse(raw, prof)
    assert m.result == "draw" and m.rank_value is None
    assert tier_name(27) == "Radiant" and tier_name(0) is None


def test_valorant_skips_kill_target_modes_and_unavailable_matches():
    v = ValorantProvider(api_key="")
    prof = Profile(**copy.deepcopy(VAL_DEMO_PROFILE))
    raw = val_matches()[0]
    for mode in ("Deathmatch", "Team Deathmatch"):
        dm = copy.deepcopy(raw)
        dm["metadata"]["mode"] = mode
        assert v.parse(dm, prof) is None

    class Page:
        def get(self, url, params=None):
            return {"data": [{"is_available": False, "metadata": None}, raw]}

    v.client = Page()
    assert v.list_match_ids(prof, 10) == [raw["metadata"]["matchid"]]


def test_valorant_explains_accounts_without_recent_matches():
    from clutch.http import NotFound

    class Missing:
        def __init__(self, code):
            self.code = code

        def get(self, url, params=None):
            raise NotFound("not found", 404, {"errors": [{"code": self.code, "status": 404}]})

    v = ValorantProvider(api_key="", client=Missing(24))
    with pytest.raises(NotFound, match="no recent matches"):
        v.resolve("Someone#NA1")
    v.client = Missing(22)  # genuinely unknown account keeps the generic message
    with pytest.raises(NotFound, match="^not found$"):
        v.resolve("Nobody#0000")


def test_rocket_league_adapter_uses_rlstats():
    rl = RocketLeagueProvider(api_key="")
    prof = rl.demo_profile()
    raws = rl.demo_matches()
    ms = [m for m in (rl.parse(r, prof) for r in raws) if m]
    assert len(ms) == len(raws)
    m = ms[0]
    assert m.character == "Octane" and m.rank_value is not None
    assert {"goals", "saves", "pct_zero_boost"} <= set(m.metrics)
    assert any(r.is_self for r in m.scoreboard)


def test_providers_report_configuration(monkeypatch):
    assert not LeagueProvider(api_key="").configured()
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-x")
    assert LeagueProvider().configured()
