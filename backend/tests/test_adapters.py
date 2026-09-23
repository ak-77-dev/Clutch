import copy

import pytest

from clutch.games import deadlock, dota
from clutch.games.deadlock import DeadlockProvider
from clutch.games.demo_data import (
    DEADLOCK_DEMO_PROFILE,
    DOTA_DEMO_PROFILE,
    LOL_DEMO_PROFILE,
    VAL_DEMO_PROFILE,
    deadlock_matches,
    dota_matches,
    lol_matches,
    val_matches,
)
from clutch.games.dota import DotaProvider
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


def test_valorant_kill_target_modes_and_unavailable_matches():
    v = ValorantProvider(api_key="")
    prof = Profile(**copy.deepcopy(VAL_DEMO_PROFILE))
    raw = val_matches()[0]
    dm = copy.deepcopy(raw)
    dm["metadata"]["mode"] = "Deathmatch"  # free-for-all: no teams, no result
    assert v.parse(dm, prof) is None
    tdm = copy.deepcopy(raw)
    tdm["metadata"]["mode"] = "Team Deathmatch"  # kept, minus the per-round stats
    m = v.parse(tdm, prof)
    assert m.metrics["acs"] is None and m.metrics["adr"] is None and m.metrics["deaths_per_round"] is None
    assert m.metrics["kd"] > 0 and m.result in ("win", "loss")

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


# ── Dota 2 ──────────────────────────────────────────────────────────────────


def test_dota_medals_and_ids():
    assert dota.medal(42) == ("Archon 2", 16.0)
    assert dota.medal(80)[0] == "Immortal" and dota.medal(None) == (None, None)
    assert dota.account_id_from("76561198181164587") == 220898859  # SteamID64 -> friend ID
    assert dota.account_id_from("220898859") == 220898859 and dota.account_id_from("Miracle-") is None
    assert dota.hero(1)[0] == "Anti-Mage" and dota.hero(99999) == ("Hero 99999", None, None)


def test_dota_parse():
    d = DotaProvider()
    prof = Profile(**copy.deepcopy(DOTA_DEMO_PROFILE))
    raw = dota_matches()[0]
    m = d.parse(raw, prof)
    me = next(p for p in raw["players"] if str(p["account_id"]) == prof.key)
    assert m.metrics["gpm"] == me["gold_per_min"]
    assert m.metrics["last_hits_per_min"] == pytest.approx(me["last_hits"] / (raw["duration"] / 60), abs=0.01)
    assert m.result == ("win" if raw["radiant_win"] == me["isRadiant"] else "loss")
    assert len(m.scoreboard) == 10 and sum(r.is_self for r in m.scoreboard) == 1
    assert m.items and all(u.startswith("https://") for u in m.items)
    assert m.mode in {"Ranked All Pick", "Unranked All Pick", "Turbo"}
    assert d.parse(raw, Profile(game="dota2", key="1", name="stranger")) is None


def test_dota_unparsed_match_reports_damage_as_unknown():
    d = DotaProvider()
    prof = Profile(**copy.deepcopy(DOTA_DEMO_PROFILE))
    raw = dota_matches()[0]
    for p in raw["players"]:
        p["hero_damage"] = p["tower_damage"] = None
    m = d.parse(raw, prof)
    assert m.metrics["damage_per_min"] is None and m.metrics["tower_damage"] is None
    assert m.metrics["gpm"] > 0


def test_dota_fetch_keeps_only_scoreboard_fields():
    full = {**dota_matches()[0], "objectives": [1] * 1000, "chat": ["gg"]}
    full["players"] = [{**p, "purchase_log": [{}] * 50} for p in full["players"]]

    class Fake:
        def get(self, url, params=None):
            return full

    slim = DotaProvider(client=Fake()).fetch_match(Profile(game="dota2", key="1", name="x"), "1")
    assert "objectives" not in slim and "purchase_log" not in slim["players"][0]
    assert slim["players"][0]["hero_id"] == full["players"][0]["hero_id"]


def test_dota_demo_rank_matches_history():
    d = DotaProvider()
    rank = d.demo_profile().ranks[0]
    ranked = [m for m in (d.parse(r, d.demo_profile()) for r in d.demo_matches()) if m and m.rank_label]
    assert rank["label"] == max(ranked, key=lambda m: m.date).rank_label


# ── Deadlock ────────────────────────────────────────────────────────────────


def test_deadlock_badges():
    assert deadlock.badge(56) == ("Mystic 6", 29.0, "https://api.deadlock-api.com/v1/assets/ranks/5/6/image")
    assert deadlock.badge(0) == (None, None, None) and deadlock.badge(57)[0] is None  # subranks stop at 6


def test_deadlock_slim_match_keeps_final_stats():
    raw = {
        "match_info": {
            "match_id": 5,
            "start_time": 1,
            "duration_s": 1800,
            "winning_team": 1,
            "match_mode": 4,
            "game_mode": 1,
            "damage_matrix": {"huge": True},
            "players": [
                {
                    "account_id": 7,
                    "team": 1,
                    "hero_id": 1,
                    "kills": 3,
                    "death_details": [{}] * 20,
                    "stats": [{"player_damage": 10}, {"player_damage": 900, "shots_hit": 5}],
                    "player_rank_data": {"initial_display_rank": 43},
                }
            ],
        }
    }
    slim = deadlock.slim_match(raw)
    assert "damage_matrix" not in slim and "death_details" not in slim["players"][0]
    assert slim["players"][0]["stats"]["player_damage"] == 900 and slim["players"][0]["badge"] == 43


def test_deadlock_parse():
    d = DeadlockProvider()
    prof = Profile(**copy.deepcopy(DEADLOCK_DEMO_PROFILE))
    raw = deadlock_matches()[0]
    m = d.parse(raw, prof)
    me = next(p for p in raw["players"] if str(p["account_id"]) == prof.key)
    st = me["stats"]
    assert m.metrics["souls_per_min"] == pytest.approx(me["net_worth"] / (raw["duration_s"] / 60), abs=0.1)
    assert m.metrics["accuracy"] == pytest.approx(100 * st["shots_hit"] / (st["shots_hit"] + st["shots_missed"]), abs=0.1)
    assert m.result == ("win" if me["team"] == raw["winning_team"] else "loss")
    assert len(m.scoreboard) == 12 and m.character in {h[0] for h in deadlock.HEROES.values()}
    no_shots = copy.deepcopy(raw)
    for p in no_shots["players"]:
        p["stats"] = {}
    assert d.parse(no_shots, prof).metrics["accuracy"] is None


def test_deadlock_fetch_attaches_player_names():
    raw = {"match_info": {**deadlock_matches()[0]}}
    calls = []

    class Fake:
        def get(self, url, params=None):
            calls.append(url)
            if url.endswith("/metadata"):
                return raw
            return [{"account_id": int(a), "personaname": f"p{a}"} for a in params["account_ids"].split(",")]

    d = DeadlockProvider(client=Fake())
    slim = d.fetch_match(Profile(game="deadlock", key="1", name="x"), "1")
    assert all(p["name"] == f"p{p['account_id']}" for p in slim["players"])
    d.fetch_match(Profile(game="deadlock", key="1", name="x"), "1")
    assert len(calls) == 3  # names are cached across matches


# ── Call of Duty (experimental) ─────────────────────────────────────────────


def test_cod_player_ids():
    from clutch.games.cod import parse_player_id
    from clutch.http import NotFound

    assert parse_player_id("Nightfall#8123456") == ("uno", "Nightfall#8123456")
    assert parse_player_id("Nightfall#1234") == ("battle", "Nightfall#1234")
    assert parse_player_id("psn:Some Name") == ("psn", "Some Name")
    with pytest.raises(NotFound):
        parse_player_id("no tag at all")


def test_cod_parse_and_auth_errors():
    from clutch.games.cod import CodProvider
    from clutch.games.demo_data import COD_DEMO_PROFILE, cod_matches
    from clutch.http import NotFound

    cod = CodProvider(token="")
    prof = Profile(**copy.deepcopy(COD_DEMO_PROFILE))
    raw = cod_matches()[0]
    m = cod.parse(raw, prof)
    st = raw["playerStats"]
    assert m.metrics["kd"] == round(st["kills"] / max(st["deaths"], 1), 2)
    assert m.metrics["accuracy"] == pytest.approx(100 * st["shotsLanded"] / st["shotsFired"], abs=0.1)
    assert m.result == raw["result"] and m.character == m.mode
    assert not cod.configured()

    class Denied:
        def get(self, url, params=None):
            return {"status": "error", "data": {"message": "Not permitted: not authenticated"}}

    with pytest.raises(NotFound, match="SSO token"):
        CodProvider(token="x", client=Denied()).resolve("Name#1234567")

    class Ok:
        def get(self, url, params=None):
            if url.endswith("/profile/type/mp"):
                return {"status": "success", "data": {"level": 42, "prestige": 2}}
            return {"status": "success", "data": {"matches": [raw]}}

    live = CodProvider(token="x", title="bo7", client=Ok())
    p = live.resolve("Name#1234567")
    assert p.key == "uno:Name#1234567" and p.ranks[0]["label"] == "Prestige 2"
    assert live.list_match_ids(p, 10) == [raw["matchID"]]
    assert live.fetch_match(p, raw["matchID"]) is raw
