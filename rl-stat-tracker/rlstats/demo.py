"""Synthetic ballchasing-format replays for demos and tests.

Generates a believable season for one player: evening play sessions, three
ranked playlists, a regular duo partner, MMR that moves with results, and
stats that genuinely correlate with winning (boost starvation and poor
positioning lose games; fatigue sets in late in long sessions). The output is
the same JSON shape as ``GET /api/replays/{id}``, so it exercises the real
parse -> analytics -> dashboard pipeline end to end.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from rlstats.ranks import tier_name

DEMO_PLAYER = {"name": "SkyReach", "platform": "steam", "id": "76561198000000001"}

PLAYLISTS = (
    # id, name, team size, share of games, starting MMR
    ("ranked-doubles", "Ranked Doubles", 2, 0.58, 1085.0),
    ("ranked-standard", "Ranked Standard", 3, 0.27, 1010.0),
    ("ranked-duels", "Ranked Duels", 1, 0.15, 905.0),
)
MAPS = ("DFH Stadium", "Mannfield", "Champions Field", "Urban Central", "Beckwith Park", "Utopia Coliseum", "Neo Tokyo")
CARS = ("Octane", "Fennec", "Dominus", "Octane", "Fennec", "Octane")
FRIENDS = (
    {"name": "Vortex", "platform": "epic", "id": "e1a2b3c4d5", "skill": 0.35},
    {"name": "kaiju", "platform": "steam", "id": "76561198000000077", "skill": -0.15},
    {"name": "Nimbus", "platform": "psynet", "id": "p9f8e7d6", "skill": 0.05},
)
_NAMES = (
    "Ghost",
    "Lumen",
    "Rift",
    "Tempo",
    "Blitz",
    "Sable",
    "Orbit",
    "Quill",
    "Nova",
    "Havoc",
    "Echo",
    "Pyro",
    "Drift",
    "Mako",
    "Zephyr",
    "Cobalt",
    "Talon",
    "Jinx",
    "Onyx",
    "Flare",
    "Rook",
    "Vex",
    "Kite",
    "Axiom",
)


def _rank(mmr: float) -> dict[str, Any]:
    value = max(1.0, min(21.99, 1 + (mmr - 200) / 85))
    tier = int(value)
    division = min(4, int((value - tier) * 4) + 1)
    return {"id": f"tier-{tier}", "tier": tier, "division": division, "name": f"{tier_name(tier)} Div {division}"}


def _poisson(rng: random.Random, lam: float) -> int:
    lam = max(lam, 0.0)
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def _player(
    rng: random.Random,
    who: dict[str, Any],
    *,
    skill: float,
    won: bool,
    team_goals: int,
    share: float,
    team_size: int,
    mmr: float,
    form: float,
) -> dict[str, Any]:
    """One player's stat block. ``form`` < 0 = a bad game (starved of boost, out of position)."""
    goals = sum(1 for _ in range(team_goals) if rng.random() < share)
    shots = goals + _poisson(rng, 1.4 + 0.5 * skill + 0.3 * form)
    assists = min(team_goals - goals, _poisson(rng, 0.45 * (team_goals - goals) + 0.1)) if team_size > 1 else 0
    saves = _poisson(rng, 1.3 + (0.4 if not won else 0) + 0.3 * skill)
    demos = _poisson(rng, 0.55 + 0.25 * max(skill, 0) + (0.3 if team_size == 3 else 0))
    score = 100 * goals + 50 * assists + 50 * saves + 20 * shots + 10 * demos + rng.randint(40, 170)
    zero_boost = max(2.0, rng.gauss(10.5 - 3.0 * form - 1.2 * skill, 2.6))
    behind_ball = min(92.0, max(40.0, rng.gauss(64 + 5.5 * form + 2 * skill, 5.0)))
    supersonic = max(2.0, rng.gauss(12 + 2.5 * skill + 1.5 * form, 3.0))
    high_air = max(0.5, rng.gauss(4.5 + 1.8 * skill + (mmr - 1000) / 250, 1.4))
    off_third = max(8.0, rng.gauss(29 + 2.0 * form, 4.0))
    return {
        "name": who["name"],
        "id": {"platform": who["platform"], "id": who["id"]},
        "car_name": who.get("car") or rng.choice(CARS),
        "rank": _rank(mmr),
        "mvp": False,
        "stats": {
            "core": {
                "score": score,
                "goals": goals,
                "assists": assists,
                "saves": saves,
                "shots": shots,
                "shooting_percentage": round(100 * goals / shots, 1) if shots else 0.0,
                "mvp": False,
            },
            "boost": {
                "bpm": round(rng.gauss(385 + 25 * skill + 15 * form, 30), 1),
                "avg_amount": round(rng.gauss(44 + 3 * form, 5), 1),
                "amount_stolen": round(max(0.0, rng.gauss(420 + 60 * form, 140)), 0),
                "percent_zero_boost": round(zero_boost, 2),
                "percent_full_boost": round(max(1.0, rng.gauss(8 + form, 2.5)), 2),
            },
            "movement": {
                "avg_speed": round(rng.gauss(1540 + 45 * skill + 25 * form, 55), 0),
                "percent_supersonic_speed": round(supersonic, 2),
                "percent_ground": round(max(40.0, rng.gauss(58 - 2 * skill, 4)), 2),
                "percent_high_air": round(high_air, 2),
            },
            "positioning": {
                "percent_defensive_third": round(max(20.0, rng.gauss(46 - 2.0 * form, 5)), 2),
                "percent_offensive_third": round(off_third, 2),
                "percent_behind_ball": round(behind_ball, 2),
                "avg_distance_to_ball": round(rng.gauss(2650 - 60 * form, 180), 0),
            },
            "demo": {"inflicted": demos, "taken": _poisson(rng, 0.8)},
        },
    }


def generate_replays(games: int = 320, seed: int = 7, end: datetime | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    end = end or datetime(2026, 9, 21, 23, 0, tzinfo=timezone.utc)
    mmr = {pid: start for pid, *_, start in PLAYLISTS}
    me = {**DEMO_PLAYER, "car": "Octane"}
    replays: list[dict[str, Any]] = []

    # Build session start times backwards from `end`: evenings, some days off.
    sessions: list[tuple[datetime, int]] = []
    day = end.replace(hour=0, minute=0)
    remaining = games
    while remaining > 0:
        if rng.random() < 0.72:
            n = min(remaining, max(2, int(rng.gauss(7, 3))))
            start = day + timedelta(hours=rng.choice((17, 18, 19, 20, 21)), minutes=rng.randint(0, 59))
            sessions.append((start, n))
            remaining -= n
        day -= timedelta(days=1)
    sessions.reverse()

    game_no = 0
    for start, n in sessions:
        clock = start
        playlist = rng.choices(PLAYLISTS, weights=[p[3] for p in PLAYLISTS])[0]
        duo = rng.choice(FRIENDS[:2]) if rng.random() < 0.75 else None
        for idx in range(n):
            if idx and rng.random() < 0.2:  # switch playlists mid-session sometimes
                playlist = rng.choices(PLAYLISTS, weights=[p[3] for p in PLAYLISTS])[0]
            pid, pname, size, _, _ = playlist
            game_no += 1
            improvement = 1.1 * game_no / games - 0.35  # the player gets better over the season
            fatigue = -0.18 * max(0, idx - 4)  # long sessions hurt
            form = rng.gauss(0, 1) + fatigue
            mates = []
            if size >= 2:
                if duo and size == 2:
                    mates = [duo]
                else:
                    mates = [dict(f) for f in rng.sample(FRIENDS, k=min(size - 1, 1))] if rng.random() < 0.5 else []
                while len(mates) < size - 1:
                    mates.append(
                        {
                            "name": rng.choice(_NAMES) + str(rng.randint(1, 99)),
                            "platform": "epic",
                            "id": f"r{rng.getrandbits(40):x}",
                            "skill": rng.gauss(0, 0.4),
                        }
                    )
            team_strength = 0.9 * improvement + 0.45 * form + sum(m.get("skill", 0) for m in mates) * 0.4 / max(size - 1, 1)
            p_win = 1 / (1 + math.exp(-(team_strength + 0.05)))
            won = rng.random() < p_win
            w_goals = max(1, _poisson(rng, 3.2 if size > 1 else 2.6))
            l_goals = rng.randint(0, w_goals - 1)
            overtime = w_goals - l_goals == 1 and rng.random() < 0.28
            mine, theirs = (w_goals, l_goals) if won else (l_goals, w_goals)
            share = 1 / size + (0.08 if size > 1 else 0)

            my_team = [
                _player(
                    rng,
                    me,
                    skill=0.2 + improvement,
                    won=won,
                    team_goals=mine,
                    share=share,
                    team_size=size,
                    mmr=mmr[pid],
                    form=form,
                )
            ]
            for mt in mates:
                my_team.append(
                    _player(
                        rng,
                        mt,
                        skill=mt.get("skill", 0),
                        won=won,
                        team_goals=mine,
                        share=(1 - share) / max(size - 1, 1),
                        team_size=size,
                        mmr=mmr[pid],
                        form=rng.gauss(0, 1),
                    )
                )
            opp_team = [
                _player(
                    rng,
                    {
                        "name": rng.choice(_NAMES) + str(rng.randint(1, 99)),
                        "platform": rng.choice(("epic", "steam", "psynet")),
                        "id": f"o{rng.getrandbits(40):x}",
                    },
                    skill=rng.gauss(0, 0.4),
                    won=not won,
                    team_goals=theirs,
                    share=1 / size,
                    team_size=size,
                    mmr=mmr[pid],
                    form=rng.gauss(0, 1),
                )
                for _ in range(size)
            ]
            winners = my_team if won else opp_team
            max(winners, key=lambda p: p["stats"]["core"]["score"])["mvp"] = True
            for p in my_team + opp_team:
                p["stats"]["core"]["mvp"] = p["mvp"]

            duration = 300 + (rng.randint(15, 240) if overtime else 0)
            color = "blue" if rng.random() < 0.5 else "orange"
            other = "orange" if color == "blue" else "blue"
            replays.append(
                {
                    "id": f"{rng.getrandbits(128):032x}"[:8] + "-demo-" + f"{game_no:04d}",
                    "status": "ok",
                    "date": clock.isoformat().replace("+00:00", "Z"),
                    "playlist_id": pid,
                    "playlist_name": pname,
                    "team_size": size,
                    "duration": duration,
                    "overtime": overtime,
                    "map_name": rng.choice(MAPS),
                    color: {"color": color, "players": my_team, "stats": {"core": {"goals": mine}}},
                    other: {"color": other, "players": opp_team, "stats": {"core": {"goals": theirs}}},
                }
            )
            mmr[pid] += (9 if won else -9) + rng.gauss(0, 1.5)
            clock += timedelta(seconds=duration + rng.randint(60, 240))
    return replays
