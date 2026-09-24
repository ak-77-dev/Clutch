"""The second wave of games: chess (Chess.com, Lichess), TFT, PUBG, CS2 (FACEIT),
Brawl Stars, Clash Royale and osu!."""

from __future__ import annotations

import pytest

from clutch.games import default_providers
from clutch.games.chess import ChessComProvider, LichessProvider, opening_family, pgn_result, slim_chesscom
from clutch.games.cs2 import Cs2Provider, map_name
from clutch.games.osu import OsuProvider, mods_label
from clutch.games.pubg import PubgProvider, mode_label, slim_match
from clutch.games.supercell import BrawlStarsProvider, ClashRoyaleProvider, battle_time, normalize_tag, win_condition
from clutch.games.tft import TftProvider, main_trait, trait_name
from clutch.http import NotFound
from clutch.models import Profile

NEW = ("chesscom", "lichess", "tft", "pubg", "cs2", "brawlstars", "clashroyale", "osu")


class FakeClient:
    """Answers JsonClient.get / get_ndjson from a url-substring -> payload table."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def _find(self, url: str) -> object:
        self.calls.append(url)
        for part, payload in self.routes.items():
            if part in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise NotFound(url)

    def get(self, url, params=None, *, headers=None):
        return self._find(url)

    def get_ndjson(self, url, params=None):
        return self._find(url)


@pytest.mark.parametrize("game", NEW)
def test_demo_seasons_parse_into_declared_metrics(game):
    p = next(x for x in default_providers() if x.meta.id == game)
    profile = p.demo_profile()
    raws = p.demo_matches()
    matches = [p.parse(r, profile) for r in raws]
    assert all(matches), "every demo match includes the demo player"
    assert len({p.match_id_of(r) for r in raws}) == len(raws), "ids are unique"
    declared = {m.key for m in p.meta.metrics}
    for m in matches:
        assert set(m.metrics) <= declared
        assert m.result in ("win", "loss", "draw", "remake")
        assert m.date.endswith("Z")
    wins = sum(m.result == "win" for m in matches) / len(matches)
    assert 0.25 < wins < 0.8  # the demo should be a believable season
    assert len({m.character for m in matches}) >= 3
    assert profile.ranks and profile.ranks[0]["label"]


def test_every_game_has_a_distinct_id_and_the_ui_fields():
    metas = [p.meta for p in default_providers()]
    assert len({m.id for m in metas}) == len(metas) == 14
    for m in metas:
        keys = {x.key for x in m.metrics}
        assert set(m.kpis) | set(m.card_metrics) | set(m.trend_metrics) | set(m.factor_metrics) <= keys


# ── chess ────────────────────────────────────────────────────────────────────


def test_opening_families():
    assert opening_family("Sicilian Defense: Najdorf Variation") == "Sicilian Defense"
    assert opening_family("https://www.chess.com/openings/English-Opening-Reversed-Sicilian-Taimanov...11.axb4-Be6") == "English Opening"
    assert opening_family("https://www.chess.com/openings/Caro-Kann-Defense-Advance-Variation") == "Caro Kann Defense"
    assert opening_family(None) == "Unknown opening"


def test_pgn_result_is_whites_score_first():
    assert pgn_result("win", "black") == "0–1" and pgn_result("win", "white") == "1–0"
    assert pgn_result("loss", "black") == "1–0" and pgn_result("draw", "white") == "½–½"


def test_chesscom_slim_and_parse():
    game = {
        "url": "https://www.chess.com/game/live/1",
        "uuid": "u-1",
        "end_time": 1790202248,
        "rated": True,
        "accuracies": {"white": 89.2, "black": 95.9},
        "time_class": "blitz",
        "rules": "chess",
        "fen": "8/8/8/8/8/8/8/8 w - - 0 33",
        "pgn": '[UTCDate "2026.09.23"]\n[UTCTime "22:19:32"]\n[ECOUrl "https://www.chess.com/openings/English-Opening"]\n[Termination "x"]',
        "white": {"username": "alex", "rating": 3056, "result": "timeout"},
        "black": {"username": "Hikaru", "rating": 3443, "result": "win"},
    }
    slim = slim_chesscom(game)
    assert "pgn" not in slim and slim["moves"] == 33
    m = ChessComProvider(client=FakeClient({})).parse(slim, type("P", (), {"key": "hikaru"})())
    assert m.result == "win" and m.role == "Black" and m.score_line == "0–1"
    assert m.metrics["accuracy"] == 95.9 and m.metrics["rating_edge"] == 387
    assert m.metrics["minutes"] == pytest.approx(4.6, abs=0.1) and m.character == "English Opening"


def test_chesscom_timeout_vs_insufficient_is_a_draw():
    slim = {
        "uuid": "u",
        "white": {"username": "a", "result": "timevsinsufficient"},
        "black": {"username": "b", "result": "timevsinsufficient"},
    }
    m = ChessComProvider(client=FakeClient({})).parse(slim, type("P", (), {"key": "a"})())
    assert m.result == "draw"


def test_lichess_lists_games_from_ndjson_and_parses_analysis():
    game = {
        "id": "abc",
        "rated": True,
        "variant": "standard",
        "speed": "blitz",
        "createdAt": 1_000_000,
        "lastMoveAt": 1_360_000,
        "status": "resign",
        "winner": "black",
        "players": {
            "white": {"user": {"name": "Other", "id": "other"}, "rating": 2644, "ratingDiff": -5},
            "black": {
                "user": {"name": "Me", "id": "me"},
                "rating": 3145,
                "ratingDiff": 8,
                "analysis": {"accuracy": 93, "acpl": 18, "blunder": 1, "mistake": 3},
            },
        },
        "opening": {"name": "Alekhine Defense: Sämisch Attack"},
        "moves": "e4 Nf6 e5 Nd5 Nc3",
    }
    p = LichessProvider(client=FakeClient({"/api/games/user/": [game]}))
    prof = type("P", (), {"key": "me"})()
    assert p.list_match_ids(prof, 10) == ["abc"]
    raw = p.fetch_match(prof, "abc")
    assert "moves" not in raw and raw["plies"] == 5
    m = p.parse(raw, prof)
    assert m.result == "win" and m.score_line == "0–1" and m.metrics["moves"] == 3
    assert m.metrics["accuracy"] == 93 and m.metrics["rating_change"] == 8 and m.character == "Alekhine Defense"
    assert m.duration_s == 360


def test_lichess_aborted_game_is_void():
    g = {"id": "x", "status": "aborted", "players": {"white": {"user": {"id": "me"}}, "black": {"user": {"id": "o"}}}}
    assert LichessProvider(client=FakeClient({})).parse(g, type("P", (), {"key": "me"})()).result == "remake"


def test_chess_usernames_are_validated_before_any_request():
    with pytest.raises(NotFound):
        ChessComProvider(client=FakeClient({})).resolve("no spaces allowed")


# ── TFT ──────────────────────────────────────────────────────────────────────


def test_tft_traits_and_top4():
    assert trait_name("TFT15_StarGuardian") == "Star Guardian"
    assert (
        main_trait(
            [
                {"name": "TFT15_A", "style": 1, "tier_current": 1, "num_units": 2},
                {"name": "TFT15_BattleAcademia", "style": 3, "tier_current": 2, "num_units": 5},
            ]
        )
        == "Battle Academia"
    )
    assert main_trait([{"name": "TFT15_A", "tier_current": 0}]) == "No comp"
    p = TftProvider(api_key="k")
    raw = p.demo_matches()[0]
    prof = p.demo_profile()
    m = p.parse(raw, prof)
    me = next(x for x in raw["info"]["participants"] if x["puuid"] == prof.key)
    assert (m.result == "win") == (me["placement"] <= 4) and m.metrics["top4"] in (0.0, 100.0)
    assert p.match_id_of(raw) == raw["metadata"]["match_id"]


# ── PUBG ─────────────────────────────────────────────────────────────────────


def test_pubg_slims_the_jsonapi_document():
    doc = {
        "data": {
            "id": "m1",
            "attributes": {
                "createdAt": "2026-09-20T20:00:00Z",
                "duration": 1800,
                "gameMode": "squad-fpp",
                "mapName": "Desert_Main",
                "matchType": "competitive",
            },
        },
        "included": [
            {
                "type": "participant",
                "id": "p1",
                "attributes": {
                    "stats": {
                        "playerId": "me",
                        "name": "Me",
                        "kills": 4,
                        "headshotKills": 1,
                        "damageDealt": 512.7,
                        "winPlace": 3,
                        "timeSurvived": 1500,
                        "walkDistance": 2500,
                        "rideDistance": 1000,
                    }
                },
            },
            {"type": "participant", "id": "p2", "attributes": {"stats": {"playerId": "mate", "name": "Mate", "kills": 1, "winPlace": 3}}},
            {"type": "participant", "id": "p3", "attributes": {"stats": {"playerId": "foe", "name": "Foe", "kills": 9, "winPlace": 1}}},
            {
                "type": "roster",
                "id": "r1",
                "attributes": {"stats": {"rank": 3}},
                "relationships": {"participants": {"data": [{"id": "p1"}, {"id": "p2"}]}},
            },
            {
                "type": "roster",
                "id": "r2",
                "attributes": {"stats": {"rank": 1}},
                "relationships": {"participants": {"data": [{"id": "p3"}]}},
            },
            {"type": "asset", "id": "telemetry"},
        ],
    }
    slim = slim_match(doc)
    assert slim["id"] == "m1" and len(slim["rosters"]) == 2
    m = PubgProvider(api_key="k").parse(slim, type("P", (), {"key": "me"})())
    assert m.character == "Miramar" and m.mode == "Ranked Squad FPP" and m.result == "win"  # top 10
    assert m.metrics["damage"] == 513 and m.metrics["headshot_pct"] == 25.0 and m.metrics["distance_km"] == 3.5
    assert m.teammates == ["Mate"] and m.score_line == "#3 of 2"
    assert mode_label("duo", None) == "Duo"


def test_pubg_player_lookup_caches_recent_matches():
    player = {"id": "account.1", "attributes": {"name": "Shroud"}, "relationships": {"matches": {"data": [{"id": "a"}, {"id": "b"}]}}}
    fake = FakeClient(
        {
            "/seasons/cur/ranked": {
                "data": {
                    "attributes": {
                        "rankedGameModeStats": {
                            "squad-fpp": {
                                "currentTier": {"tier": "Gold", "subTier": "2"},
                                "currentRankPoint": 2240,
                                "roundsPlayed": 40,
                                "wins": 4,
                            }
                        }
                    }
                }
            },
            "/seasons": {"data": [{"id": "cur", "attributes": {"isCurrentSeason": True}}]},
            "/shards/steam/players": {"data": [player]},
        }
    )
    p = PubgProvider(api_key="k", client=fake)
    prof = p.resolve("Shroud")
    assert prof.ranks[0]["label"] == "Gold 2" and prof.ranks[0]["losses"] == 36
    assert p.list_match_ids(prof, 10) == ["a", "b"]


# ── CS2 / FACEIT ─────────────────────────────────────────────────────────────


def test_cs2_parse_string_stats():
    raw = {
        "match_id": "1-abc",
        "started_at": 1_790_000_000,
        "finished_at": 1_790_002_400,
        "competition_name": "CS2 5v5",
        "rounds": [
            {
                "round_stats": {"Map": "de_nuke", "Rounds": "22"},
                "teams": [
                    {
                        "team_stats": {"Team": "A", "Final Score": "13", "Team Win": "1"},
                        "players": [
                            {
                                "player_id": "me",
                                "nickname": "Me",
                                "player_stats": {
                                    "Kills": "22",
                                    "Deaths": "11",
                                    "Assists": "4",
                                    "ADR": "96.5",
                                    "Headshots %": "55",
                                    "MVPs": "5",
                                    "Triple Kills": "2",
                                    "Quadro Kills": "1",
                                },
                            }
                        ],
                    },
                    {
                        "team_stats": {"Team": "B", "Final Score": "9", "Team Win": "0"},
                        "players": [{"player_id": "o", "nickname": "O", "player_stats": {"Kills": "10"}}],
                    },
                ],
            }
        ],
    }
    m = Cs2Provider(api_key="k").parse(raw, type("P", (), {"key": "me"})())
    assert m.result == "win" and m.score_line == "13–9" and m.character == "Nuke"
    assert m.metrics["kd"] == 2.0 and m.metrics["kr"] == 1.0 and m.metrics["adr"] == 96.5 and m.metrics["multikills"] == 3
    assert m.duration_s == 2400 and map_name("de_dust2") == "Dust2"
    assert Cs2Provider(api_key="k").parse({**raw, "rounds": []}, type("P", (), {"key": "me"})()) is None


def test_cs2_players_without_cs2_are_rejected():
    fake = FakeClient({"/players": {"player_id": "x", "nickname": "Csgo_only", "games": {"csgo": {}}}})
    with pytest.raises(NotFound, match="hasn't played CS2"):
        Cs2Provider(api_key="k", client=fake).resolve("Csgo_only")


# ── Supercell ────────────────────────────────────────────────────────────────


def test_supercell_tags_times_and_ids():
    assert normalize_tag(" #2pp0jc ") == "#2PP0JC" and normalize_tag("2PPOJC") == "#2PP0JC"
    with pytest.raises(NotFound):
        normalize_tag("#HELLO")
    assert battle_time("20260921T193012.000Z") == "2026-09-21T19:30:12Z"
    p = BrawlStarsProvider(api_key="k")
    b = {"battleTime": "20260921T193012.000Z", "battle": {"teams": [[{"tag": "#A"}], [{"tag": "#B"}]]}}
    assert p._with_id(b)["id"] == p._with_id({**b, "battle": {"teams": [[{"tag": "#B"}], [{"tag": "#A"}]]}})["id"]


def test_brawl_showdown_rank_and_battlelog_cache():
    items = [
        {
            "battleTime": "20260921T193012.000Z",
            "event": {"mode": "soloShowdown", "map": "Skull Creek"},
            "battle": {
                "mode": "soloShowdown",
                "rank": 3,
                "trophyChange": 7,
                "players": [
                    {"tag": "#ME", "name": "Me", "brawler": {"id": 16000000, "name": "SHELLY", "power": 11, "trophies": 900}},
                    {"tag": "#X", "name": "X", "brawler": {"id": 16000001, "name": "COLT"}},
                ],
            },
        },
    ]
    p = BrawlStarsProvider(api_key="k", client=FakeClient({"/battlelog": {"items": items}}))
    prof = type("P", (), {"key": "#ME"})()
    ids = p.list_match_ids(prof, 25)
    m = p.parse(p.fetch_match(prof, ids[0]), prof)
    assert m.result == "win" and m.metrics["showdown_rank"] == 3 and m.character == "Shelly" and m.mode == "Solo Showdown"
    assert m.character_icon.endswith("/16000000.png")


def test_clash_royale_win_condition_and_side_swap():
    assert win_condition([{"name": "Zap"}, {"name": "Hog Rider"}]) == "Hog Rider"
    raw = {
        "id": "x",
        "type": "pathOfLegend",
        "battleTime": "20260921T193012.000Z",
        "team": [{"tag": "#OPP", "crowns": 1, "cards": [{"name": "Golem", "level": 14, "maxLevel": 16}]}],
        "opponent": [
            {"tag": "#ME", "crowns": 3, "elixirLeaked": 1.5, "cards": [{"name": "Miner", "level": 11, "maxLevel": 14, "elixirCost": 3}]}
        ],
    }
    m = ClashRoyaleProvider(api_key="k").parse(raw, type("P", (), {"key": "#ME"})())
    assert m.result == "win" and m.score_line == "3–1" and m.character == "Miner" and m.mode == "Path of Legend"
    assert m.metrics["level_edge"] == -1.0  # 11/14 is level 13 on the 16 scale vs 14


# ── osu! ─────────────────────────────────────────────────────────────────────


def test_osu_mods_and_parse():
    assert mods_label([{"acronym": "HD"}, {"acronym": "DT"}]) == "HD+DT" and mods_label([{"acronym": "CL"}]) == "No mod"
    raw = {
        "id": 42,
        "user_id": 7,
        "accuracy": 0.9677,
        "passed": False,
        "rank": "F",
        "pp": None,
        "max_combo": 120,
        "ended_at": "2026-09-21T19:30:12+00:00",
        "statistics": {"great": 300, "ok": 20, "miss": 9},
        "beatmap": {"version": "Extra", "difficulty_rating": 6.1, "total_length": 180},
        "beatmapset": {"title": "Ghost", "artist": "Camellia", "covers": {"list": "c.jpg"}},
    }
    m = OsuProvider(client_id="i", client_secret="s").parse(raw, Profile(game="osu", key="7", name="Me"))
    assert m.result == "loss" and m.metrics["accuracy"] == 96.77 and m.metrics["pp"] is None and m.metrics["misses"] == 9
    assert m.date == "2026-09-21T19:30:12Z" and m.character == "Ghost" and m.role == "Extra"


def test_osu_needs_both_client_values(monkeypatch):
    monkeypatch.delenv("OSU_CLIENT_ID", raising=False)
    monkeypatch.delenv("OSU_CLIENT_SECRET", raising=False)
    assert not OsuProvider().configured()
    assert OsuProvider(client_id="i", client_secret="s").configured()


def test_osu_token_is_fetched_once_and_sent(monkeypatch):
    posts = []

    class Resp:
        status_code = 200

        def json(self):
            return {"access_token": "tok", "expires_in": 86400}

    monkeypatch.setattr("clutch.games.osu.requests.post", lambda *a, **k: posts.append(k) or Resp())
    p = OsuProvider(client_id="i", client_secret="s")
    fake = FakeClient({"/users/@peppy/osu": {"id": 2, "username": "peppy", "statistics": {"global_rank": 1234, "pp": 5000.4}}})
    fake.session = type("S", (), {"headers": {}})()
    p.client = fake
    prof = p.resolve("peppy")
    p.resolve("peppy")
    assert len(posts) == 1 and fake.session.headers["Authorization"] == "Bearer tok"
    assert prof.key == "2" and prof.ranks[0]["label"] == "#1,234 · 5,000pp"
