"""Demo seasons for the second wave of games, in each upstream API's exact shape.

Like ``demo_data``: deterministic, with real cause and effect for the analytics
to find (accuracy wins chess games, levelling on time wins TFT lobbies, damage
and staying alive place you high in PUBG, ADR wins CS2 maps, ...).
"""

from __future__ import annotations

import copy
import math
import random
from datetime import timedelta
from functools import lru_cache
from typing import Any

from clutch.games.chess import PIECES, TC_PIECE
from clutch.games.demo_data import _HANDLES, _poisson, _sessions


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _pick(rng: random.Random, weighted: list[tuple[Any, float]]) -> Any:
    return rng.choices([v for v, _ in weighted], weights=[w for _, w in weighted])[0]


def _name(rng: random.Random) -> str:
    return f"{rng.choice(_HANDLES)}{rng.randint(1, 999)}"


# ── Chess ────────────────────────────────────────────────────────────────────
# Accuracy decides games; the Caro-Kann is this player's weak spot as Black,
# and blitz late at night goes badly.

OPENINGS = [
    ("B90", "Sicilian Defense: Najdorf Variation", "Sicilian-Defense-Najdorf-Variation", 0.25, 18),
    ("C50", "Italian Game: Giuoco Piano", "Italian-Game-Giuoco-Piano", 0.3, 22),
    ("D30", "Queen's Gambit Declined", "Queens-Gambit-Declined", 0.1, 15),
    ("B12", "Caro-Kann Defense: Advance Variation", "Caro-Kann-Defense-Advance-Variation", -0.6, 12),
    ("C18", "French Defense: Winawer Variation", "French-Defense-Winawer-Variation", 0.0, 10),
    ("C65", "Ruy Lopez: Berlin Defense", "Ruy-Lopez-Opening-Berlin-Defense", 0.05, 12),
    ("E90", "King's Indian Defense", "Kings-Indian-Defense", -0.1, 11),
]
CHESS_TC = [("blitz", 55, 300), ("rapid", 30, 600), ("bullet", 15, 60)]

CHESSCOM_DEMO_PROFILE: dict[str, Any] = {
    "game": "chesscom",
    "key": "demo:nightfall_chess",
    "name": "Nightfall_Chess",
    "region": "US",
    "demo": True,
    "icon": None,
    "ranks": [],  # filled from the history so the ratings match
}
LICHESS_DEMO_PROFILE: dict[str, Any] = {"game": "lichess", "key": "demo:nightfall", "name": "Nightfall", "demo": True, "ranks": []}


def _chess_games(seed: int, games: int) -> list[dict[str, Any]]:
    """Site-neutral games: the per-site shapes are built from these."""
    rng = random.Random(seed)
    ratings = {"blitz": 1480, "rapid": 1560, "bullet": 1390}
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            tc, secs = _pick(rng, [((t, s), w) for t, w, s in CHESS_TC])
            eco, name, slug, edge, _ = _pick(rng, [(o, o[4]) for o in OPENINGS])
            white = rng.random() < 0.5
            late = clock.hour >= 22
            form = rng.gauss(0, 1) - 0.25 * max(0, idx - 3) - (0.5 if late and tc == "blitz" else 0) + 0.8 * n / games
            opp = int(rng.gauss(ratings[tc], 90))
            accuracy = min(98.0, max(45.0, rng.gauss(79 + 5 * form + (edge * 4 if not white else 0), 5)))
            opp_acc = min(98.0, max(45.0, rng.gauss(79 + (opp - ratings[tc]) / 40, 5)))
            p = _sig((accuracy - opp_acc) / 4 + (0.15 if white else -0.15) + (edge if not white else 0.1))
            roll = rng.random()
            result = "win" if roll < p * 0.93 else "draw" if roll < p * 0.93 + 0.07 else "loss"
            k = 22
            expected = _sig((ratings[tc] - opp) / 173.7)
            score = {"win": 1.0, "draw": 0.5, "loss": 0.0}[result]
            diff = round(k * (score - expected))
            moves = max(12, int(rng.gauss(38 if result == "draw" else 34, 10)))
            length = min(secs * 2, moves * secs / 40 * rng.uniform(1.2, 2.2))
            out.append(
                {
                    "n": n,
                    "start": clock,
                    "length": length,
                    "tc": tc,
                    "secs": secs,
                    "white": white,
                    "rating": ratings[tc],
                    "diff": diff,
                    "opp": opp,
                    "opp_name": _name(rng),
                    "result": result,
                    "accuracy": round(accuracy, 2),
                    "opp_acc": round(opp_acc, 2),
                    "opening": (eco, name, slug),
                    "moves": moves,
                    "analysed": rng.random() < 0.65,
                    "blunders": max(0, int(rng.gauss(2.2 - 0.6 * form, 1))),
                    "mistakes": max(0, int(rng.gauss(2.5 - 0.5 * form, 1.2))),
                    "acpl": max(8, int(rng.gauss(55 - 9 * form, 9))),
                }
            )
            ratings[tc] += diff
            clock += timedelta(seconds=length + rng.uniform(40, 180))
    return out


@lru_cache(maxsize=1)
def _chesscom_cached() -> tuple[dict[str, Any], ...]:
    me = CHESSCOM_DEMO_PROFILE["name"]
    out = []
    for g in _chess_games(seed=21, games=170):
        mine = g["rating"] + g["diff"]
        mine_result = {"win": "win", "loss": "resigned", "draw": "agreed"}[g["result"]]
        opp_result = {"win": "resigned", "loss": "win", "draw": "agreed"}[g["result"]]
        me_side = {"username": me, "rating": mine, "result": mine_result}
        opp_side = {"username": g["opp_name"], "rating": g["opp"], "result": opp_result}
        eco, _, slug = g["opening"]
        start = g["start"].timestamp()
        out.append(
            {
                "url": f"https://www.chess.com/game/live/{150_000_000_000 + g['n'] * 7919}",
                "uuid": f"demo-{g['n']:05d}-chesscom",
                "end_time": int(start + g["length"]),
                "start_time": start,
                "rated": True,
                "accuracies": {
                    "white": g["accuracy"] if g["white"] else g["opp_acc"],
                    "black": g["opp_acc"] if g["white"] else g["accuracy"],
                }
                if g["analysed"]
                else None,
                "time_class": g["tc"],
                "time_control": str(g["secs"]),
                "rules": "chess",
                "white": me_side if g["white"] else opp_side,
                "black": opp_side if g["white"] else me_side,
                "eco": f"https://www.chess.com/openings/{slug}",
                "moves": g["moves"],
                "termination": f"{me if g['result'] == 'win' else g['opp_name']} won by resignation"
                if g["result"] != "draw"
                else "Game drawn by agreement",
                "opening": f"https://www.chess.com/openings/{slug}",
            }
        )
    return tuple(out)


def chesscom_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_chesscom_cached()))


@lru_cache(maxsize=1)
def _lichess_cached() -> tuple[dict[str, Any], ...]:
    me = {"name": LICHESS_DEMO_PROFILE["name"], "id": LICHESS_DEMO_PROFILE["name"].lower()}
    out = []
    for g in _chess_games(seed=23, games=160):
        eco, name, _ = g["opening"]
        start_ms = int(g["start"].timestamp() * 1000)
        analysis = (
            {
                "inaccuracy": g["mistakes"] + 1,
                "mistake": g["mistakes"],
                "blunder": g["blunders"],
                "acpl": g["acpl"],
                "accuracy": round(g["accuracy"]),
            }
            if g["analysed"]
            else None
        )
        opp_analysis = {"inaccuracy": 3, "mistake": 2, "blunder": 1, "acpl": 50, "accuracy": round(g["opp_acc"])} if g["analysed"] else None
        mine = {"user": me, "rating": g["rating"] + 150, "ratingDiff": g["diff"], **({"analysis": analysis} if analysis else {})}
        theirs = {
            "user": {"name": g["opp_name"], "id": g["opp_name"].lower()},
            "rating": g["opp"] + 150,
            "ratingDiff": -g["diff"],
            **({"analysis": opp_analysis} if opp_analysis else {}),
        }
        white = g["white"]
        winner = None if g["result"] == "draw" else ("white" if (g["result"] == "win") == white else "black")
        out.append(
            {
                "id": f"dm{g['n']:06d}",
                "rated": True,
                "variant": "standard",
                "speed": g["tc"],
                "perf": g["tc"],
                "createdAt": start_ms,
                "lastMoveAt": start_ms + int(g["length"] * 1000),
                "status": "draw" if winner is None else "resign",
                "players": {"white": mine if white else theirs, "black": theirs if white else mine},
                **({"winner": winner} if winner else {}),
                "opening": {"eco": eco, "name": name, "ply": 6},
                "plies": g["moves"] * 2 - (0 if white else 1),
            }
        )
    return tuple(out)


def lichess_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_lichess_cached()))


def chess_demo_ranks(games: list[dict[str, Any]], key: str, site: str) -> list[dict[str, Any]]:
    """Latest rating per time control, most-played first (matches the history)."""
    latest: dict[str, tuple[int, int]] = {}
    for g in games:
        if site == "chesscom":
            me = g["white"] if g["white"]["username"].lower() == key else g["black"]
            rating, tc = me["rating"], g["time_class"]
        else:
            me = next(p for p in g["players"].values() if p["user"]["id"] == key)
            rating, tc = me["rating"] + me["ratingDiff"], g["speed"]
        count = latest.get(tc, (0, 0))[1] + 1
        latest[tc] = (rating, count)
    out = [
        {
            "queue": tc.title(),
            "label": str(r),
            "tier": tc,
            "lp": None,
            "value": float(r),
            "icon": PIECES.format(TC_PIECE.get(tc, "wK")),
            "wins": None,
            "losses": None,
            "games": c,
        }
        for tc, (r, c) in latest.items()
    ]
    return sorted(out, key=lambda r: -r["games"])


# ── Teamfight Tactics ────────────────────────────────────────────────────────
# Hitting level 8 with little gold banked places well; Juggernaut comps are this
# player's comfort pick, Sniper comps their trap.

TFT_DEMO_PROFILE: dict[str, Any] = {
    "game": "tft",
    "key": "demo-tft-nightfall",
    "name": "Nightfall",
    "tag": "NA1",
    "level": 402,
    "region": "na1",
    "demo": True,
    "ranks": [{"queue": "Ranked TFT", "label": "Diamond III", "tier": "diamond", "lp": 41, "value": 25.41, "wins": 61, "losses": 66}],
}
TFT_COMPS = [
    ("TFT15_Juggernaut", 0.5, 26),
    ("TFT15_StarGuardian", 0.2, 22),
    ("TFT15_SoulFighter", 0.1, 18),
    ("TFT15_BattleAcademia", 0.0, 14),
    ("TFT15_Sniper", -0.6, 12),
    ("TFT15_Mentor", 0.15, 8),
]
TFT_UNITS = [
    "TFT15_Ahri",
    "TFT15_Garen",
    "TFT15_Jinx",
    "TFT15_Lux",
    "TFT15_Sett",
    "TFT15_Yasuo",
    "TFT15_Kaisa",
    "TFT15_Ezreal",
    "TFT15_Poppy",
    "TFT15_Lulu",
]


def _tft_participant(rng: random.Random, puuid: str, name: str, strength: float, comp: str) -> dict[str, Any]:
    level = max(6, min(10, round(rng.gauss(7.9 + 0.5 * strength, 0.6))))
    return {
        "puuid": puuid,
        "riotIdGameName": name,
        "riotIdTagline": "NA1",
        "level": level,
        "gold_left": max(0, int(rng.gauss(8 - 5 * strength, 6))),
        "units": [{"character_id": rng.choice(TFT_UNITS), "tier": rng.choice((1, 2, 2, 3))} for _ in range(level)],
        "traits": [
            {"name": comp, "num_units": rng.randint(4, 7), "style": rng.choice((2, 3, 3, 4)), "tier_current": 2},
            {"name": rng.choice(TFT_COMPS)[0], "num_units": 2, "style": 1, "tier_current": 1},
        ],
        "_strength": strength + rng.gauss(0, 0.7) + 0.4 * (level - 8) - 0.04 * max(0, 20 - level),
    }


@lru_cache(maxsize=1)
def _tft_cached(games: int = 140, seed: int = 29) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            comp, edge, _ = _pick(rng, [(c, c[2]) for c in TFT_COMPS])
            form = rng.gauss(0, 0.8) + edge - 0.15 * max(0, idx - 3) + 0.5 * n / games
            me = _tft_participant(rng, TFT_DEMO_PROFILE["key"], TFT_DEMO_PROFILE["name"], form, comp)
            others = [
                _tft_participant(rng, f"demo-tft-{rng.getrandbits(40):x}", _name(rng), rng.gauss(0, 0.8), rng.choice(TFT_COMPS)[0])
                for _ in range(7)
            ]
            lobby = sorted([me, *others], key=lambda p: -p["_strength"])
            length = rng.uniform(1900, 2350)
            for place, p in enumerate(lobby, start=1):
                p["placement"] = place
                p["last_round"] = int(38 - 2.2 * (place - 1) + rng.uniform(-1, 1))
                p["time_eliminated"] = length * (1 - 0.07 * (place - 1))
                p["players_eliminated"] = max(0, int(rng.gauss(2.2 - 0.3 * place, 0.8)))
                p["total_damage_to_players"] = max(0, int(rng.gauss(150 - 14 * place, 18)))
                del p["_strength"]
            out.append(
                {
                    "metadata": {"match_id": f"NA1_{5_300_000_000 + n * 1373}", "participants": [p["puuid"] for p in lobby]},
                    "info": {
                        "game_datetime": int(clock.timestamp() * 1000),
                        "game_length": length,
                        "queue_id": 1100,
                        "tft_game_type": "standard",
                        "tft_set_number": 15,
                        "participants": lobby,
                    },
                }
            )
            clock += timedelta(seconds=length + rng.uniform(90, 300))
    return tuple(out)


def tft_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_tft_cached()))


# ── PUBG ─────────────────────────────────────────────────────────────────────
# Damage and time alive drive placement; hot-dropping on Miramar goes badly.

PUBG_DEMO_PROFILE: dict[str, Any] = {
    "game": "pubg",
    "key": "account.demo0000000000000000000000000001",
    "name": "Nightfall",
    "region": "steam",
    "demo": True,
    "ranks": [{"queue": "Ranked Squad FPP", "label": "Gold 2", "tier": "gold", "lp": 2240, "value": 2240.0, "wins": 9, "losses": 71}],
}
PUBG_MAPS = [
    ("Baltic_Main", 0.2, 35),
    ("Desert_Main", -0.5, 20),
    ("Savage_Main", 0.1, 15),
    ("Tiger_Main", 0.0, 15),
    ("Neon_Main", 0.05, 15),
]


def _pubg_player(rng: random.Random, pid: str, name: str, skill: float, place: int, teams: int) -> dict[str, Any]:
    alive_frac = max(0.03, min(1.0, 1 - (place - 1) / teams + rng.gauss(0, 0.05)))
    kills = _poisson(rng, max(0.1, 1.1 + 1.2 * skill + 2.5 * alive_frac))
    return {
        "name": name,
        "playerId": pid,
        "kills": kills,
        "assists": _poisson(rng, 0.6),
        "DBNOs": _poisson(rng, kills * 0.9 + 0.3),
        "damageDealt": max(0.0, rng.gauss(kills * 105 + 60 + 80 * skill, 60)),
        "headshotKills": sum(rng.random() < 0.28 for _ in range(kills)),
        "longestKill": rng.uniform(10, 250) if kills else 0,
        "timeSurvived": 1800 * alive_frac,
        "walkDistance": 2600 * alive_frac + rng.uniform(0, 800),
        "rideDistance": rng.uniform(0, 4000),
        "swimDistance": 0,
        "heals": _poisson(rng, 3 * alive_frac),
        "boosts": _poisson(rng, 2.5 * alive_frac),
        "winPlace": place,
    }


@lru_cache(maxsize=1)
def _pubg_cached(games: int = 120, seed: int = 31) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            mp, edge, _ = _pick(rng, [(m, m[2]) for m in PUBG_MAPS])
            teams = rng.randint(22, 26)
            skill = rng.gauss(0, 0.8) + edge - 0.12 * max(0, idx - 3) + 0.4 * n / games
            place = max(1, min(teams, int(teams * (1 - _sig(skill * 1.4 - 0.2)) + rng.gauss(0, 2.5))))
            rosters = []
            for rank in range(1, teams + 1):
                if rank == place:
                    players = [_pubg_player(rng, PUBG_DEMO_PROFILE["key"], PUBG_DEMO_PROFILE["name"], skill, place, teams)]
                    players += [
                        _pubg_player(rng, f"account.demo{rng.getrandbits(64):032x}", _name(rng), rng.gauss(0, 0.7), place, teams)
                        for _ in range(3)
                    ]
                elif rank <= 10:
                    players = [
                        _pubg_player(rng, f"account.demo{rng.getrandbits(64):032x}", _name(rng), rng.gauss(0.3, 0.7), rank, teams)
                        for _ in range(4)
                    ]
                else:
                    players = []  # teams outside the top 10 aren't shown, so the demo doesn't carry them
                rosters.append({"rank": rank, "players": players})
            length = rng.uniform(1650, 1950)
            ranked = rng.random() < 0.35
            out.append(
                {
                    "id": f"demo-pubg-{n:05d}",
                    "createdAt": clock.isoformat().replace("+00:00", "Z"),
                    "duration": int(length),
                    "gameMode": "squad-fpp",
                    "mapName": mp,
                    "matchType": "competitive" if ranked else "official",
                    "rosters": rosters,
                }
            )
            clock += timedelta(seconds=length + rng.uniform(60, 240))
    return tuple(out)


def pubg_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_pubg_cached()))


# ── Counter-Strike 2 (FACEIT) ────────────────────────────────────────────────
# ADR and headshot rate win maps; Nuke is the weak map, Mirage the comfort pick.

CS2_DEMO_PROFILE: dict[str, Any] = {
    "game": "cs2",
    "key": "demo-faceit-nightfall",
    "name": "Nightfall",
    "region": "NA",
    "level": 8,
    "demo": True,
    "ranks": [
        {
            "queue": "FACEIT",
            "label": "Level 8 · 1784 Elo",
            "tier": "level 8",
            "lp": 1784,
            "value": 1784.0,
            "icon": "https://cdn-frontend.faceit-cdn.net/web/static/media/assets_images_skill-icons_skill_level_8_svg.svg",
            "wins": None,
            "losses": None,
        }
    ],
}
CS2_MAPS = [
    ("de_mirage", 0.35, 25),
    ("de_inferno", 0.1, 20),
    ("de_ancient", 0.0, 15),
    ("de_dust2", 0.15, 15),
    ("de_nuke", -0.55, 12),
    ("de_anubis", -0.1, 13),
]


def _cs2_line(rng: random.Random, pid: str, nick: str, skill: float, rounds: int) -> dict[str, Any]:
    kr = max(0.2, rng.gauss(0.72 + 0.12 * skill, 0.12))
    kills = int(kr * rounds)
    return {
        "player_id": pid,
        "nickname": nick,
        "player_stats": {
            "Kills": str(kills),
            "Deaths": str(max(3, int(rng.gauss(0.7 - 0.08 * skill, 0.1) * rounds))),
            "Assists": str(_poisson(rng, 0.15 * rounds)),
            "ADR": f"{max(30.0, rng.gauss(78 + 14 * skill, 12)):.1f}",
            "Headshots %": str(max(10, min(80, int(rng.gauss(46 + 6 * skill, 9))))),
            "MVPs": str(_poisson(rng, max(0.2, 0.09 * rounds + skill))),
            "Triple Kills": str(_poisson(rng, max(0.05, 0.8 + 0.5 * skill))),
            "Quadro Kills": str(_poisson(rng, max(0.02, 0.15 + 0.1 * skill))),
            "Penta Kills": str(1 if rng.random() < 0.02 + 0.02 * skill else 0),
        },
    }


@lru_cache(maxsize=1)
def _cs2_cached(games: int = 130, seed: int = 37) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            mp, edge, _ = _pick(rng, [(m, m[2]) for m in CS2_MAPS])
            skill = rng.gauss(0, 0.9) + edge - 0.15 * max(0, idx - 2) + 0.5 * n / games
            won = rng.random() < _sig(1.1 * skill)
            loser = rng.randint(3, 11) if rng.random() > 0.08 else 12
            win_score = 13 if loser < 12 else rng.choice((13, 16))
            rounds = win_score + loser
            mine, theirs = (win_score, loser) if won else (loser, win_score)
            me = _cs2_line(rng, CS2_DEMO_PROFILE["key"], CS2_DEMO_PROFILE["name"], skill, rounds)
            team = [me] + [
                _cs2_line(rng, f"demo-{rng.getrandbits(48):x}", _name(rng), rng.gauss(0.2 if won else -0.2, 0.6), rounds) for _ in range(4)
            ]
            enemy = [
                _cs2_line(rng, f"demo-{rng.getrandbits(48):x}", _name(rng), rng.gauss(-0.2 if won else 0.2, 0.6), rounds) for _ in range(5)
            ]
            minutes = rounds * rng.uniform(1.6, 2.0)
            out.append(
                {
                    "match_id": f"1-demo-{n:05d}",
                    "started_at": int(clock.timestamp()),
                    "finished_at": int(clock.timestamp() + minutes * 60),
                    "game_mode": "5v5",
                    "competition_name": "CS2 5v5",
                    "results": {"winner": "faction1" if won else "faction2", "score": {"faction1": mine, "faction2": theirs}},
                    "rounds": [
                        {
                            "round_stats": {
                                "Map": mp,
                                "Rounds": str(rounds),
                                "Score": f"{mine} / {theirs}",
                                "Winner": "faction1" if won else "faction2",
                            },
                            "teams": [
                                {
                                    "team_id": "faction1",
                                    "team_stats": {"Team": "team_Nightfall", "Final Score": str(mine), "Team Win": "1" if won else "0"},
                                    "players": team,
                                },
                                {
                                    "team_id": "faction2",
                                    "team_stats": {"Team": "team_Opponents", "Final Score": str(theirs), "Team Win": "0" if won else "1"},
                                    "players": enemy,
                                },
                            ],
                        }
                    ],
                }
            )
            clock += timedelta(minutes=minutes + rng.uniform(3, 8))
    return tuple(out)


def cs2_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_cs2_cached()))


# ── Brawl Stars ──────────────────────────────────────────────────────────────
# Higher-power brawlers win; Mortis is the one this player keeps losing with.

BRAWL_DEMO_PROFILE: dict[str, Any] = {
    "game": "brawlstars",
    "key": "#2PP0JCV",
    "name": "Nightfall",
    "tag": "2PP0JCV",
    "level": 214,
    "region": "Night Owls",
    "icon": "https://cdn.brawlify.com/profile-icons/regular/28000000.png",
    "demo": True,
    "ranks": [{"queue": "Trophies", "label": "38,412 🏆", "tier": "trophies", "lp": 39020, "value": 38412.0, "wins": 9120, "losses": None}],
}
BRAWLERS = [("SHELLY", 16000000, 0.1, 12), ("COLT", 16000001, 0.15, 14), ("EL PRIMO", 16000010, 0.2, 15), ("MORTIS", 16000011, -0.6, 12),
            ("PIPER", 16000015, 0.25, 14), ("GENE", 16000021, 0.1, 10), ("LEON", 16000023, 0.05, 12), ("POCO", 16000013, 0.0, 11)]  # fmt: skip
BRAWL_MODES = [
    ("gemGrab", "Hard Rock Mine", 30),
    ("brawlBall", "Backyard Bowl", 25),
    ("knockout", "Goldarm Gulch", 20),
    ("heist", "Safe Zone", 10),
    ("soloShowdown", "Skull Creek", 15),
]


def _brawler(rng: random.Random, pick: tuple[str, int, float, int], power: int) -> dict[str, Any]:
    return {"id": pick[1], "name": pick[0], "power": power, "trophies": int(rng.gauss(700 + 40 * power, 90))}


@lru_cache(maxsize=1)
def _brawl_cached(games: int = 150, seed: int = 41) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            pick = _pick(rng, [(b, b[3]) for b in BRAWLERS])
            mode, map_name, _ = _pick(rng, [(m, m[2]) for m in BRAWL_MODES])
            power = rng.choice((9, 10, 10, 11, 11, 11))
            skill = rng.gauss(0, 0.8) + pick[2] + 0.25 * (power - 10) - 0.12 * max(0, idx - 4) + 0.3 * n / games
            me = {"tag": BRAWL_DEMO_PROFILE["key"], "name": BRAWL_DEMO_PROFILE["name"], "brawler": _brawler(rng, pick, power)}
            duration = int(rng.uniform(90, 180))
            stamp = clock.strftime("%Y%m%dT%H%M%S.000Z")
            if mode == "soloShowdown":
                rank = max(1, min(10, int(5.5 - 3 * skill + rng.gauss(0, 1.5))))
                players = [me] + [
                    {
                        "tag": f"#{rng.getrandbits(30):X}",
                        "name": _name(rng),
                        "brawler": _brawler(rng, rng.choice(BRAWLERS), rng.randint(8, 11)),
                    }
                    for _ in range(9)
                ]
                battle = {
                    "mode": mode,
                    "type": "ranked",
                    "rank": rank,
                    "duration": duration,
                    "trophyChange": [12, 9, 7, 4, 2, 0, -2, -4, -6, -8][rank - 1],
                    "players": players,
                }
            else:
                won = rng.random() < _sig(1.2 * skill)
                draw = not won and rng.random() < 0.05
                mates = [
                    {
                        "tag": f"#{rng.getrandbits(30):X}",
                        "name": _name(rng),
                        "brawler": _brawler(rng, rng.choice(BRAWLERS), rng.randint(9, 11)),
                    }
                    for _ in range(2)
                ]
                foes = [
                    {
                        "tag": f"#{rng.getrandbits(30):X}",
                        "name": _name(rng),
                        "brawler": _brawler(rng, rng.choice(BRAWLERS), rng.randint(9, 11)),
                    }
                    for _ in range(3)
                ]
                star = me if won and rng.random() < _sig(skill) else rng.choice(mates if won else foes)
                battle = {
                    "mode": mode,
                    "type": "ranked",
                    "result": "draw" if draw else "victory" if won else "defeat",
                    "duration": duration,
                    "trophyChange": 0 if draw else (8 if won else -6),
                    "starPlayer": star,
                    "teams": [[me, *mates], foes],
                }
            out.append(
                {
                    "battleTime": stamp,
                    "event": {
                        "id": 15_000_000 + BRAWL_MODES.index(next(m for m in BRAWL_MODES if m[0] == mode)),
                        "mode": mode,
                        "map": map_name,
                    },
                    "battle": battle,
                }
            )
            clock += timedelta(seconds=duration + rng.uniform(30, 120))
    from clutch.games.supercell import BrawlStarsProvider

    p = BrawlStarsProvider(api_key="demo")
    return tuple(p._with_id(b) for b in out)


def brawl_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_brawl_cached()))


# ── Clash Royale ─────────────────────────────────────────────────────────────
# Card levels and not leaking elixir win; the Golem deck is too slow for this
# player's style.

ROYALE_DEMO_PROFILE: dict[str, Any] = {
    "game": "clashroyale",
    "key": "#8LQ2VRUC",
    "name": "Nightfall",
    "tag": "8LQ2VRUC",
    "level": 52,
    "region": "Night Owls",
    "demo": True,
    "ranks": [
        {
            "queue": "Trophy road",
            "label": "8,640 🏆 · Legendary Arena",
            "tier": "trophies",
            "lp": 9012,
            "value": 8640.0,
            "wins": 4102,
            "losses": 3688,
        },
        {
            "queue": "Path of Legend",
            "label": "League 7 · 1420 rating",
            "tier": "path of legend",
            "lp": 1420,
            "value": 8420.0,
            "wins": None,
            "losses": None,
        },
    ],
}
ROYALE_CARDS = {
    "Hog Rider": 4, "Golem": 8, "Miner": 3, "X-Bow": 6, "Balloon": 5, "Musketeer": 4, "Fireball": 4, "The Log": 2, "Ice Spirit": 1,
    "Skeletons": 1, "Cannon": 3, "Valkyrie": 4, "Baby Dragon": 4, "Night Witch": 4, "Lightning": 6, "Tornado": 3, "Mega Minion": 3,
    "Inferno Dragon": 4, "Poison": 4, "Knight": 3, "Archers": 3, "Zap": 2, "Tesla": 4, "Electro Wizard": 4,
}  # fmt: skip
ROYALE_DECKS = [
    ("Hog Rider", ["Hog Rider", "Musketeer", "Valkyrie", "Cannon", "Fireball", "The Log", "Ice Spirit", "Skeletons"], 0.35, 40),
    ("Golem", ["Golem", "Night Witch", "Baby Dragon", "Lightning", "Tornado", "Mega Minion", "Zap", "Archers"], -0.5, 20),
    ("Miner", ["Miner", "Poison", "Valkyrie", "Electro Wizard", "Inferno Dragon", "The Log", "Skeletons", "Knight"], 0.15, 25),
    ("X-Bow", ["X-Bow", "Tesla", "Archers", "Knight", "Fireball", "The Log", "Ice Spirit", "Skeletons"], 0.0, 15),
]


def _royale_card(rng: random.Random, name: str, level: int) -> dict[str, Any]:
    slug = name.lower().replace(" ", "-").replace(".", "").replace("the-", "the-")
    return {
        "name": name,
        "level": level,
        "maxLevel": 16,
        "elixirCost": ROYALE_CARDS.get(name, 3),
        "iconUrls": {"medium": f"https://cdn.royaleapi.com/static/img/cards-150/{slug}.png"},
    }


@lru_cache(maxsize=1)
def _royale_cached(games: int = 150, seed: int = 43) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    all_cards = list(ROYALE_CARDS)
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            _, deck, edge, _ = _pick(rng, [(d, d[3]) for d in ROYALE_DECKS])
            my_lvl, opp_lvl = rng.choice((13, 14, 14, 15)), rng.choice((13, 14, 14, 15))
            leak = max(0.0, rng.gauss(2.5, 1.6))
            skill = rng.gauss(0, 0.8) + edge + 0.45 * (my_lvl - opp_lvl) - 0.25 * leak + 0.3 * n / games - 0.12 * max(0, idx - 4)
            won = rng.random() < _sig(1.3 * skill)
            draw = rng.random() < 0.04
            mine = rng.choice((1, 2, 3, 3)) if won else (rng.choice((0, 1)) if not draw else 1)
            theirs = rng.choice((0, 1, 1)) if won else (rng.choice((1, 2, 3)) if not draw else 1)
            if won and theirs >= mine:
                theirs = mine - 1
            if not won and not draw and mine >= theirs:
                mine = theirs - 1
            ladder = rng.random() < 0.6
            opp_deck = rng.sample(all_cards, 8)
            out.append(
                {
                    "type": "PvP" if ladder else "pathOfLegend",
                    "battleTime": clock.strftime("%Y%m%dT%H%M%S.000Z"),
                    "gameMode": {"id": 72000006 if ladder else 72000464, "name": "Ladder" if ladder else "Ranked1v1_NewArena"},
                    "arena": {"name": "Legendary Arena"},
                    "team": [
                        {
                            "tag": ROYALE_DEMO_PROFILE["key"],
                            "name": ROYALE_DEMO_PROFILE["name"],
                            "crowns": max(0, mine),
                            "trophyChange": (30 if won else -29 if not draw else 0) if ladder else None,
                            "elixirLeaked": round(leak, 2),
                            "cards": [_royale_card(rng, c, my_lvl) for c in deck],
                        }
                    ],
                    "opponent": [
                        {
                            "tag": f"#{rng.getrandbits(32):X}",
                            "name": _name(rng),
                            "crowns": max(0, theirs),
                            "elixirLeaked": round(max(0.0, rng.gauss(2.5, 1.5)), 2),
                            "cards": [_royale_card(rng, c, opp_lvl) for c in opp_deck],
                        }
                    ],
                }
            )
            clock += timedelta(seconds=rng.uniform(150, 330))
    from clutch.games.supercell import ClashRoyaleProvider

    p = ClashRoyaleProvider(api_key="demo")
    return tuple(p._with_id(b) for b in out)


def royale_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_royale_cached()))


# ── osu! ─────────────────────────────────────────────────────────────────────
# Maps above ~5.5 stars break this player's accuracy; long maps cause misses.

OSU_DEMO_PROFILE: dict[str, Any] = {
    "game": "osu",
    "key": "9000001",
    "name": "Nightfall",
    "region": "US",
    "level": 101,
    "demo": True,
    "ranks": [
        {
            "queue": "osu! standard",
            "label": "#48,210 · 4,512pp",
            "tier": "global",
            "lp": 9120,
            "value": 4512.0,
            "wins": None,
            "losses": None,
        }
    ],
}
OSU_SETS = [
    ("Camellia", "Ghost", 1, 5.8, 190), ("xi", "FREEDOM DiVE", 2, 6.3, 257), ("DragonForce", "Through the Fire and Flames", 3, 5.4, 440),
    ("Feryquitous", "Arcana", 4, 4.9, 150), ("ReoNa", "Anima", 5, 4.6, 230), ("LeaF", "Aleph-0", 6, 5.2, 170),
    ("Toby Fox", "MEGALOVANIA", 7, 4.2, 150), ("DECO*27", "Vampire", 8, 5.0, 210),
]  # fmt: skip
OSU_MODS = [([], 55), (["HD"], 25), (["HD", "HR"], 10), (["DT"], 10)]


@lru_cache(maxsize=1)
def _osu_cached(games: int = 170, seed: int = 47) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    out, n = [], 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            artist, title, set_n, stars, length = rng.choice(OSU_SETS)
            mods = _pick(rng, OSU_MODS)
            stars = round(stars * (1.4 if "DT" in mods else 1.08 if "HR" in mods else 1), 2)
            length = int(length / (1.5 if "DT" in mods else 1))
            skill = rng.gauss(0, 0.7) - 1.1 * max(0, stars - 5.5) - 0.002 * max(0, length - 200) + 0.4 * n / games - 0.1 * max(0, idx - 5)
            passed = rng.random() < _sig(1.6 + 1.5 * skill)
            acc = min(0.999, max(0.80, rng.gauss(0.955 + 0.02 * skill, 0.012)))
            objects = int(length * 2.4)
            misses = _poisson(rng, max(0.2, 6 - 5 * skill + length / 120))
            oks = int(objects * (1 - acc) * 2.2)
            rank = "F" if not passed else ("S" if acc > 0.96 and misses == 0 else "A" if acc > 0.93 else "B" if acc > 0.87 else "C")
            out.append(
                {
                    "id": 4_000_000_000 + n * 131,
                    "user_id": int(OSU_DEMO_PROFILE["key"]),
                    "user": {"id": int(OSU_DEMO_PROFILE["key"]), "username": OSU_DEMO_PROFILE["name"]},
                    "accuracy": round(acc, 4),
                    "max_combo": int(objects * rng.uniform(0.25, 0.95)) if passed else int(objects * rng.uniform(0.05, 0.4)),
                    "passed": passed,
                    "pp": round(stars**2.6 * 9 * acc**12 / (1 + misses * 0.08), 1) if passed else None,
                    "rank": rank,
                    "ended_at": clock.isoformat().replace("+00:00", "Z"),
                    "mods": [{"acronym": m} for m in mods],
                    "statistics": {"great": max(0, objects - oks - misses), "ok": oks, "meh": int(oks * 0.1), "miss": misses},
                    "beatmap": {
                        "id": 1_000_000 + set_n * 10,
                        "version": "Extra" if stars > 5 else "Insane",
                        "difficulty_rating": stars,
                        "total_length": length,
                    },
                    "beatmapset": {"id": 900_000 + set_n, "title": title, "artist": artist, "covers": {}},
                }
            )
            clock += timedelta(seconds=length + rng.uniform(20, 90))
    return tuple(out)


def osu_matches() -> list[dict[str, Any]]:
    return copy.deepcopy(list(_osu_cached()))
