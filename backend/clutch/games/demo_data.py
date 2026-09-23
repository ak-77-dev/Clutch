"""Deterministic demo seasons in the exact upstream JSON shapes.

- League: Riot match-v5 ``GET /lol/match/v5/matches/{id}`` payloads.
- Valorant: HenrikDev ``v3`` match objects.

Stats carry real cause and effect so the analytics have something true to
find: farming, vision and staying alive win LoL games; headshot rate and
damage win Valorant rounds; both players tire late in long sessions and
improve over the season.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

END = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)

LOL_DEMO_PROFILE: dict[str, Any] = {
    "game": "lol",
    "key": "demo-lol-nightfall",
    "name": "Nightfall",
    "tag": "NA1",
    "level": 312,
    "region": "na1",
    "demo": True,
    "ranks": [
        {"queue": "Ranked Solo/Duo", "label": "Emerald III", "tier": "emerald", "lp": 54, "value": 21.54, "wins": 88, "losses": 74},
        {"queue": "Ranked Flex", "label": "Platinum I", "tier": "platinum", "lp": 12, "value": 19.12, "wins": 19, "losses": 14},
    ],
}
VAL_DEMO_PROFILE: dict[str, Any] = {
    "game": "valorant",
    "key": "demo-val-nightfall",
    "name": "Nightfall",
    "tag": "NA1",
    "level": 187,
    "region": "na",
    "demo": True,
    "icon": "https://media.valorant-api.com/playercards/b03e3577-4f31-ae08-b9b7-4eb587fd13c3/smallart.png",
    "ranks": [{"queue": "Competitive", "label": "Platinum 3", "tier": "platinum", "lp": 61, "value": 17, "wins": None, "losses": None}],
}

_HANDLES = (
    "Rift",
    "Vex",
    "Sol",
    "Mako",
    "Ember",
    "Quill",
    "Nova",
    "Havoc",
    "Onyx",
    "Talon",
    "Pyre",
    "Juno",
    "Kite",
    "Sable",
    "Frost",
    "Drift",
    "Zeph",
    "Blitz",
    "Orbit",
    "Lumen",
    "Axiom",
    "Crest",
    "Tempo",
    "Ronin",
)


def _poisson(rng: random.Random, lam: float) -> int:
    lam = max(lam, 0.05)
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def _sessions(rng: random.Random, games: int) -> list[tuple[datetime, int]]:
    out: list[tuple[datetime, int]] = []
    day = END.replace(hour=0, minute=0)
    left = games
    while left > 0:
        if rng.random() < 0.7:
            n = min(left, max(1, int(rng.gauss(4.5, 2.2))))
            out.append((day + timedelta(hours=rng.choice((18, 19, 20, 21, 22)), minutes=rng.randint(0, 59)), n))
            left -= n
        day -= timedelta(days=1)
    return out[::-1]


def _stranger(rng: random.Random, prefix: str) -> tuple[str, str, str]:
    return (
        f"{rng.choice(_HANDLES)}{rng.randint(1, 999)}",
        rng.choice(("NA1", "NA2", "7777", "GG", "EUW")),
        f"{prefix}-{rng.getrandbits(48):012x}",
    )


# ── League of Legends ───────────────────────────────────────────────────────

LOL_POOLS = {
    "MIDDLE": [("Ahri", 30), ("Viktor", 20), ("Syndra", 15), ("Orianna", 10), ("Yasuo", 10), ("Akali", 8), ("Sylas", 7)],
    "TOP": [("Aatrox", 40), ("Camille", 30), ("Jax", 30)],
}
LOL_ANY = {
    "TOP": ["Aatrox", "Garen", "Camille", "Jax", "Darius", "Fiora", "Malphite", "Ornn", "Renekton", "Sett", "Mordekaiser"],
    "JUNGLE": ["LeeSin", "Vi", "Graves", "Kindred", "Viego", "Hecarim", "Amumu", "Sejuani"],
    "MIDDLE": [
        "Ahri",
        "Viktor",
        "Syndra",
        "Orianna",
        "Zed",
        "Katarina",
        "Veigar",
        "Annie",
        "Malzahar",
        "Lissandra",
        "Talon",
        "Fizz",
        "Zoe",
    ],
    "BOTTOM": ["Jinx", "Caitlyn", "Ezreal", "Jhin", "Lucian", "Ashe", "Kaisa", "Vayne"],
    "UTILITY": ["Thresh", "Lulu", "Nautilus", "Leona", "Nami", "Karma", "Braum", "Lux"],
}
LOL_ITEMS = {
    "TOP": [3071, 3053, 6333, 3047, 3742, 3065, 3075, 3111],
    "JUNGLE": [3071, 3142, 3814, 3047, 6333, 3053, 3111, 3161],
    "MIDDLE": [6655, 3089, 3157, 3020, 3135, 4645, 3165, 3102],
    "BOTTOM": [6672, 3031, 3006, 3094, 3036, 3072, 3087, 3153],
    "UTILITY": [3504, 3107, 3190, 3158, 6617, 2065, 3222, 3117],
}
LOL_ROLE_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def _lol_participant(
    rng: random.Random,
    *,
    puuid: str,
    name: str,
    tag: str,
    champ: str,
    role: str,
    team: int,
    won: bool,
    minutes: float,
    form: float,
    is_me: bool,
) -> dict[str, Any]:
    cs_rate = {"TOP": 7.0, "JUNGLE": 5.6, "MIDDLE": 7.4, "BOTTOM": 7.8, "UTILITY": 1.2}[role]
    cs_rate = max(0.5, rng.gauss(cs_rate + 0.55 * form, 0.6))
    kills = _poisson(rng, (5.5 if role != "UTILITY" else 2.0) + 1.6 * form + (1.2 if won else 0))
    deaths = _poisson(rng, 5.0 - 1.4 * form + (1.3 if not won else 0))
    assists = _poisson(rng, (8.5 if role != "UTILITY" else 13) + 1.3 * form + (2.5 if won else 0))
    vision = max(0.2, rng.gauss((0.95 if role != "UTILITY" else 2.3) + 0.16 * form, 0.18)) * minutes
    items = rng.sample(LOL_ITEMS[role], k=min(6, 2 + int(minutes / 6)))
    return {
        "puuid": puuid,
        "riotIdGameName": name,
        "riotIdTagline": tag,
        "summonerName": name,
        "championName": champ,
        "teamPosition": role,
        "individualPosition": role,
        "teamId": team,
        "win": won,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "totalMinionsKilled": int(cs_rate * minutes * (0.85 if role == "JUNGLE" else 1)),
        "neutralMinionsKilled": int(cs_rate * minutes * (0.15 if role == "JUNGLE" else 0.02)),
        "goldEarned": int(minutes * rng.gauss(390 + 30 * form + (25 if won else 0), 25)),
        "totalDamageDealtToChampions": int(minutes * max(150, rng.gauss(620 + 130 * form, 110)) * (0.45 if role == "UTILITY" else 1)),
        "visionScore": int(vision),
        "visionWardsBoughtInGame": _poisson(rng, (1.6 if role != "UTILITY" else 5) + 0.7 * form),
        "champLevel": min(18, int(minutes / 1.9) + rng.randint(-1, 1)),
        "summoner1Id": 4,
        "summoner2Id": 14 if role == "MIDDLE" else 12,
        **{f"item{i}": (items[i] if i < len(items) else 0) for i in range(6)},
        "item6": 3364 if role == "UTILITY" else 3340,
        "gameEndedInEarlySurrender": False,
        "challenges": {},
    }


@lru_cache(maxsize=1)
def _lol_matches_cached(games: int = 190, seed: int = 5) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    me = LOL_DEMO_PROFILE
    duo = ("Kestrel", "NA1", "demo-lol-kestrel")
    out = []
    n = 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            queue = rng.choices((420, 440, 450), weights=(70, 15, 15))[0]
            role = "MIDDLE" if rng.random() < 0.78 else "TOP"
            improvement = 0.9 * n / games - 0.15
            form = rng.gauss(0, 1) - 0.22 * max(0, idx - 2)  # fatigue after game 3
            with_duo = queue == 440 or (queue == 420 and rng.random() < 0.2)
            p_win = 1 / (1 + math.exp(-(0.95 * form + improvement + (0.35 if with_duo else 0))))
            won = rng.random() < p_win
            minutes = max(16.0, rng.gauss(27.5 - (2 if won else 0), 4.5))
            if queue == 450:
                minutes = max(12.0, rng.gauss(19, 3))
            remake = rng.random() < 0.012
            if remake:
                minutes = rng.uniform(3.2, 4.4)
            my_team = rng.choice((100, 200))
            other_team = 300 - my_team
            pool = LOL_POOLS[role]
            champ = rng.choices([c for c, _ in pool], weights=[w for _, w in pool])[0]
            # Viktor games go badly for this player (something for insights to find).
            if champ == "Viktor" and rng.random() < 0.3:
                won = False
            parts = []
            used: set[str] = {champ} if queue != 450 else set()
            for team in (my_team, other_team):
                for r in LOL_ROLE_ORDER:
                    if team == my_team and r == role:
                        who = (me["name"], me["tag"], me["key"])
                        c = champ if queue != 450 else rng.choice([x for xs in LOL_ANY.values() for x in xs if x not in used])
                        f = form
                    elif team == my_team and with_duo and r == ("JUNGLE" if role != "JUNGLE" else "TOP"):
                        who, c, f = duo, rng.choice(LOL_ANY[r]), rng.gauss(0.2, 1)
                    else:
                        who, c, f = _stranger(rng, "lol"), rng.choice(LOL_ANY[r]), rng.gauss(0, 1)
                    # Champions are unique per match; the player's own pick is reserved up front.
                    while c in used and not (who[2] == me["key"] and queue != 450):
                        c = rng.choice([x for xs in LOL_ANY.values() for x in xs])
                    used.add(c)
                    parts.append(
                        _lol_participant(
                            rng,
                            puuid=who[2],
                            name=who[0],
                            tag=who[1],
                            champ=c,
                            role=r,
                            team=team,
                            won=(team == my_team) == won,
                            minutes=minutes,
                            form=f,
                            is_me=who[2] == me["key"],
                        )
                    )
            for team in (100, 200):
                tp = [p for p in parts if p["teamId"] == team]
                tk = sum(p["kills"] for p in tp) or 1
                td = sum(p["totalDamageDealtToChampions"] for p in tp) or 1
                for p in tp:
                    p["challenges"] = {
                        "killParticipation": round((p["kills"] + p["assists"]) / tk, 3),
                        "teamDamagePercentage": round(p["totalDamageDealtToChampions"] / td, 3),
                    }
                    if remake:
                        p["gameEndedInEarlySurrender"] = True
            if queue == 450:  # ARAM has no lanes: the API reports empty positions
                for p in parts:
                    p["teamPosition"] = p["individualPosition"] = ""
            ts = int(clock.timestamp() * 1000)
            out.append(
                {
                    "metadata": {
                        "dataVersion": "2",
                        "matchId": f"NA1_{5_100_000_000 + n * 137}",
                        "participants": [p["puuid"] for p in parts],
                    },
                    "info": {
                        "gameCreation": ts - 60_000,
                        "gameStartTimestamp": ts,
                        "gameEndTimestamp": ts + int(minutes * 60_000),
                        "gameDuration": int(minutes * 60),
                        "gameMode": "ARAM" if queue == 450 else "CLASSIC",
                        "gameType": "MATCHED_GAME",
                        "gameVersion": "16.18.712.1234",
                        "mapId": 12 if queue == 450 else 11,
                        "platformId": "NA1",
                        "queueId": queue,
                        "participants": parts,
                        "teams": [{"teamId": t, "win": (t == my_team) == won} for t in (100, 200)],
                    },
                }
            )
            clock += timedelta(minutes=minutes + rng.uniform(3, 8))
    return tuple(out)


def lol_matches() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(list(_lol_matches_cached()))


# ── Valorant ────────────────────────────────────────────────────────────────

VAL_MAIN = [("Jett", 35), ("Reyna", 20), ("Raze", 15), ("Omen", 10), ("Sova", 10), ("Killjoy", 10)]
VAL_ANY = [
    "Jett",
    "Reyna",
    "Raze",
    "Phoenix",
    "Neon",
    "Sova",
    "Skye",
    "Fade",
    "Breach",
    "KAY/O",
    "Gekko",
    "Omen",
    "Brimstone",
    "Viper",
    "Astra",
    "Harbor",
    "Clove",
    "Killjoy",
    "Cypher",
    "Sage",
    "Chamber",
    "Deadlock",
    "Vyse",
]
VAL_MAPS = {"Ascent": 0.25, "Bind": 0.0, "Haven": 0.1, "Lotus": -0.45, "Sunset": 0.05, "Abyss": -0.1, "Corrode": 0.15}


def _val_player(
    rng: random.Random, *, puuid: str, name: str, tag: str, agent: str, team: str, tier: int, rounds: int, form: float, won: bool
) -> dict[str, Any]:
    from clutch.games.valorant import agent_icon, tier_name

    kills = _poisson(rng, rounds * (0.72 + 0.14 * form + (0.08 if won else 0)))
    deaths = _poisson(rng, rounds * (0.73 - 0.09 * form - (0.06 if won else 0)))
    assists = _poisson(rng, rounds * (0.24 + 0.05 * form))
    shots = max(kills * 4, 20)
    hs_pct = min(0.6, max(0.05, rng.gauss(0.21 + 0.04 * form, 0.04)))
    head = int(shots * hs_pct)
    legs = int(shots * 0.06)
    return {
        "puuid": puuid,
        "name": name,
        "tag": tag,
        "team": team,
        "level": rng.randint(20, 400),
        "character": agent,
        "currenttier": tier,
        "currenttier_patched": tier_name(tier) or "Unrated",
        "party_id": f"party-{rng.getrandbits(32):x}",
        "assets": {"agent": {"small": agent_icon(agent)}},
        "stats": {
            "score": int(rounds * max(80, rng.gauss(215 + 45 * form + (15 if won else 0), 35))),
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "headshots": head,
            "bodyshots": shots - head - legs,
            "legshots": legs,
        },
        "damage_made": int(rounds * max(50, rng.gauss(138 + 28 * form + (8 if won else 0), 18))),
        "damage_received": int(rounds * max(50, rng.gauss(140 - 20 * form, 18))),
    }


@lru_cache(maxsize=1)
def _val_matches_cached(games: int = 150, seed: int = 9) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    me = VAL_DEMO_PROFILE
    friend = ("Kestrel", "NA1", "demo-val-kestrel")
    tier = 14.0  # Gold 3
    out = []
    n = 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            mode = rng.choices(("Competitive", "Unrated", "Swiftplay"), weights=(76, 18, 6))[0]
            vmap = rng.choice(list(VAL_MAPS))
            improvement = 0.8 * n / games - 0.25
            form = rng.gauss(0, 1) - 0.2 * max(0, idx - 2)
            stack = rng.random() < 0.35
            p_win = 1 / (1 + math.exp(-(0.9 * form + improvement + VAL_MAPS[vmap] + (0.3 if stack else 0))))
            won = rng.random() < p_win
            target = 5 if mode == "Swiftplay" else 13
            if rng.random() < 0.08 and mode != "Swiftplay":
                win_r, lose_r = 14, 12
            else:
                win_r, lose_r = target, rng.randint(max(0, target - 11), target - 2)
            rounds = win_r + lose_r
            my_team, other = rng.choice((("Red", "Blue"), ("Blue", "Red")))
            agent = rng.choices([a for a, _ in VAL_MAIN], weights=[w for _, w in VAL_MAIN])[0]
            cur_tier = int(round(tier))
            used: set[str] = set()
            players = []
            for team in (my_team, other):
                for slot in range(5):
                    if team == my_team and slot == 0:
                        who, a, f = (me["name"], me["tag"], me["key"]), agent, form
                    elif team == my_team and slot == 1 and stack:
                        who, a, f = friend, rng.choice(["Omen", "Sova", "Killjoy", "Skye"]), rng.gauss(0.2, 1)
                    else:
                        who, a, f = _stranger(rng, "val"), rng.choice(VAL_ANY), rng.gauss(0, 1)
                    while a in used and team == my_team:
                        a = rng.choice(VAL_ANY)
                    if team == my_team:
                        used.add(a)
                    players.append(
                        _val_player(
                            rng,
                            puuid=who[2],
                            name=who[0],
                            tag=who[1],
                            agent=a,
                            team=team,
                            tier=max(3, cur_tier + rng.randint(-1, 1)) if who[2] != me["key"] else cur_tier,
                            rounds=rounds,
                            form=f,
                            won=(team == my_team) == won,
                        )
                    )
            length_s = rounds * rng.uniform(95, 115)
            mine = {"has_won": won, "rounds_won": win_r if won else lose_r, "rounds_lost": lose_r if won else win_r}
            theirs = {"has_won": not won, "rounds_won": mine["rounds_lost"], "rounds_lost": mine["rounds_won"]}
            out.append(
                {
                    "metadata": {
                        "map": vmap,
                        "game_version": "release-11.06",
                        "game_length": int(length_s * 1000),
                        "game_start": int(clock.timestamp()),
                        "rounds_played": rounds,
                        "mode": mode,
                        "mode_id": mode.lower(),
                        "queue": "Standard",
                        "matchid": f"{rng.getrandbits(128):032x}",
                        "region": "na",
                        "cluster": "Virginia",
                        "platform": "PC",
                    },
                    "players": {"all_players": players},
                    "teams": {my_team.lower(): mine, other.lower(): theirs},
                }
            )
            if mode == "Competitive":
                tier = min(26.0, max(3.0, tier + (0.18 if won else -0.16)))
            clock += timedelta(seconds=length_s + rng.uniform(120, 300))
    return tuple(out)


def val_matches() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(list(_val_matches_cached()))


# ── Dota 2 & Deadlock ───────────────────────────────────────────────────────
# Both are Steam games keyed by 32-bit account id; raw matches use the slimmed
# shapes the adapters store (``DotaProvider.fetch_match`` / ``slim_match``).
# Last hits and staying alive win Dota games; souls and accuracy win Deadlock.

DOTA_DEMO_PROFILE: dict[str, Any] = {
    "game": "dota2",
    "key": "900000001",
    "name": "Nightfall",
    "region": "US East",
    "demo": True,
    "icon": "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/antimage.png",
}
DEADLOCK_DEMO_PROFILE: dict[str, Any] = {
    "game": "deadlock",
    "key": "900000002",
    "name": "Nightfall",
    "region": "NA",
    "demo": True,
    "icon": "https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes/inferno_sm.webp",
}

# (hero_id, weight): Anti-Mage, Juggernaut, Phantom Assassin, Shadow Fiend, Faceless Void, Medusa
DOTA_POOL = [(1, 30), (8, 22), (44, 18), (11, 12), (41, 10), (94, 8)]
DOTA_ITEMS = [1, 48, 63, 116, 145, 147, 156, 160, 208, 249, 250]
# (hero_id, weight): Infernus, Vindicta, Kelvin, Lady Geist, Wraith
DEADLOCK_POOL = [(1, 30), (3, 25), (12, 20), (4, 15), (7, 10)]


def _steam_player(rng: random.Random) -> tuple[int, str]:
    return rng.randint(100_000_000, 899_999_999), f"{rng.choice(_HANDLES)}{rng.randint(1, 999)}"


@lru_cache(maxsize=1)
def _dota_matches_cached(games: int = 140, seed: int = 11) -> tuple[dict[str, Any], ...]:
    from clutch.games._dota_assets import HEROES

    rng = random.Random(seed)
    me = (int(DOTA_DEMO_PROFILE["key"]), DOTA_DEMO_PROFILE["name"])
    friend = (900000011, "Kestrel")
    ladder = 16.0  # medal ladder value: (medal - 1) * 5 + stars - 1 -> Archon 2
    others = [h for h in HEROES if h not in dict(DOTA_POOL)]
    out = []
    n = 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            turbo = rng.random() < 0.15
            ranked = not turbo and rng.random() < 0.8
            form = rng.gauss(0, 1) - 0.2 * max(0, idx - 2)
            stack = rng.random() < 0.3
            p_win = 1 / (1 + math.exp(-(0.95 * form + 0.8 * n / games - 0.2 + (0.3 if stack else 0))))
            won = rng.random() < p_win
            my_hero = rng.choices([h for h, _ in DOTA_POOL], weights=[w for _, w in DOTA_POOL])[0]
            if my_hero == 94 and rng.random() < 0.35:  # the comfort pick that isn't working
                won = False
            minutes = max(15.0, rng.gauss(22 if turbo else 41 - (3 if won else 0), 7))
            radiant = rng.random() < 0.5
            medal_, stars = divmod(int(ladder), 5)
            tier = (medal_ + 1) * 10 + stars + 1
            picks = rng.sample(others, 9)
            players = []
            for slot in range(10):
                on_radiant = slot < 5
                mine = on_radiant == radiant
                first = mine and slot % 5 == 0
                duo = mine and stack and slot % 5 == 1
                acct, name = me if first else friend if duo else _steam_player(rng)
                f = form if first else rng.gauss(0.2 if duo else 0, 1)
                core = slot % 5 < 3
                team_won = mine == won
                lh_rate = (6.2 + 1.1 * f + (0.6 if team_won else 0)) if core else rng.uniform(0.6, 1.6)
                players.append(
                    {
                        "account_id": acct,
                        "personaname": name,
                        "player_slot": slot if on_radiant else 128 + slot - 5,
                        "isRadiant": on_radiant,
                        "hero_id": my_hero if first else picks.pop(),
                        "kills": _poisson(rng, (7 if core else 3) + 2 * f + (2 if team_won else 0)),
                        "deaths": _poisson(rng, max(1.0, 6.5 - 1.6 * f - (1.5 if team_won else 0))),
                        "assists": _poisson(rng, (9 if core else 15) + 2 * f + (3 if team_won else 0)),
                        "last_hits": int(max(0.5, lh_rate) * minutes),
                        "denies": _poisson(rng, 8 if core else 2),
                        "gold_per_min": int(max(200, rng.gauss((560 if core else 320) + 70 * f + (60 if team_won else 0), 40))),
                        "xp_per_min": int(max(250, rng.gauss((650 if core else 450) + 60 * f + (50 if team_won else 0), 45))),
                        "hero_damage": int(minutes * max(150, rng.gauss((620 if core else 380) + 90 * f, 80))),
                        "tower_damage": int(max(0, rng.gauss((3500 if core else 700) + 1200 * f + (1500 if team_won else 0), 800))),
                        "hero_healing": 0,
                        "net_worth": int(minutes * max(200, rng.gauss(520 if core else 300, 60))),
                        "level": min(30, int(minutes / 1.6)),
                        "rank_tier": tier if first else max(11, min(75, tier + rng.choice((-10, -1, 0, 0, 1, 10)))),
                        "leaver_status": 0,
                        **{f"item_{i}": rng.choice(DOTA_ITEMS) for i in range(6)},
                        "item_neutral": 0,
                    }
                )
            out.append(
                {
                    "match_id": 8_800_000_000 + n * 7919,
                    "start_time": int(clock.timestamp()),
                    "duration": int(minutes * 60),
                    "radiant_win": radiant == won,
                    "game_mode": 23 if turbo else 22,
                    "lobby_type": 7 if ranked else 0,
                    "radiant_score": sum(p["kills"] for p in players if p["isRadiant"]),
                    "dire_score": sum(p["kills"] for p in players if not p["isRadiant"]),
                    "patch": 58,
                    "players": players,
                }
            )
            if ranked:
                ladder = min(34.0, max(0.0, ladder + (0.25 if won else -0.22)))
            clock += timedelta(minutes=minutes + rng.uniform(4, 10))
    return tuple(out)


def dota_matches() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(list(_dota_matches_cached()))


@lru_cache(maxsize=1)
def _deadlock_matches_cached(games: int = 120, seed: int = 13) -> tuple[dict[str, Any], ...]:
    from clutch.games._deadlock_assets import HEROES

    rng = random.Random(seed)
    me = (int(DEADLOCK_DEMO_PROFILE["key"]), DEADLOCK_DEMO_PROFILE["name"])
    friend = (900000012, "Kestrel")
    ladder = 20.0  # badge ladder value: (tier - 1) * 6 + subrank - 1 -> Acolyte 3
    pool = [(h, w) for h, w in DEADLOCK_POOL if h in HEROES]
    others = [h for h in HEROES if h not in dict(pool)]
    out = []
    n = 0
    for start, count in _sessions(rng, games):
        clock = start
        for idx in range(count):
            n += 1
            ranked = rng.random() < 0.7
            form = rng.gauss(0, 1) - 0.2 * max(0, idx - 2)
            stack = rng.random() < 0.3
            p_win = 1 / (1 + math.exp(-(0.95 * form + 0.7 * n / games - 0.2 + (0.3 if stack else 0))))
            won = rng.random() < p_win
            minutes = max(20.0, rng.gauss(33 - (2 if won else 0), 6))
            my_team = rng.choice((0, 1))
            my_hero = rng.choices([h for h, _ in pool], weights=[w for _, w in pool])[0]
            tier, sub = divmod(int(ladder), 6)
            my_badge = (tier + 1) * 10 + sub + 1
            picks = rng.sample(others, 11)
            players = []
            for slot in range(12):
                team = slot // 6
                mine = team == my_team
                first = mine and slot % 6 == 0
                duo = mine and stack and slot % 6 == 1
                acct, name = me if first else friend if duo else _steam_player(rng)
                f = form if first else rng.gauss(0.2 if duo else 0, 1)
                team_won = mine == won
                hits = int(minutes * max(8, rng.gauss(22 + 3 * f, 4)))
                acc = min(0.8, max(0.2, rng.gauss(0.46 + 0.05 * f + (0.02 if team_won else 0), 0.05)))
                players.append(
                    {
                        "account_id": acct,
                        "name": name,
                        "player_slot": slot,
                        "team": team,
                        "hero_id": my_hero if first else picks.pop(),
                        "kills": _poisson(rng, 6 + 2 * f + (2 if team_won else 0)),
                        "deaths": _poisson(rng, max(1.0, 7 - 1.5 * f - (1.5 if team_won else 0))),
                        "assists": _poisson(rng, 12 + 3 * f + (3 if team_won else 0)),
                        "net_worth": int(minutes * max(500, rng.gauss(1150 + 150 * f + (80 if team_won else 0), 90))),
                        "last_hits": int(minutes * max(2, rng.gauss(6 + f, 1.2))),
                        "denies": _poisson(rng, 8),
                        "level": min(36, int(minutes / 0.9)),
                        "badge": my_badge if first else max(11, my_badge + rng.choice((-10, -1, 0, 0, 1))),
                        "stats": {
                            "player_damage": int(minutes * max(300, rng.gauss(900 + 150 * f, 110))),
                            "player_damage_taken": int(minutes * max(300, rng.gauss(950 - 90 * f, 110))),
                            "player_healing": int(minutes * max(20, rng.gauss(260, 90))),
                            "boss_damage": int(max(0, rng.gauss(9000 + 3000 * f + (4000 if team_won else 0), 2500))),
                            "shots_hit": int(hits / 0.9 * acc),
                            "shots_missed": int(hits / 0.9 * (1 - acc)),
                            "hero_bullets_hit": hits,
                            "hero_bullets_hit_crit": int(hits * min(0.5, max(0.03, rng.gauss(0.14 + 0.03 * f, 0.03)))),
                        },
                    }
                )
            out.append(
                {
                    "match_id": 106_000_000 + n * 4099,
                    "start_time": int(clock.timestamp()),
                    "duration_s": int(minutes * 60),
                    "winning_team": my_team if won else 1 - my_team,
                    "match_mode": 4 if ranked else 1,
                    "game_mode": 1,
                    "players": players,
                }
            )
            if ranked:
                ladder = min(59.0, max(0.0, ladder + (0.3 if won else -0.27)))
            clock += timedelta(minutes=minutes + rng.uniform(3, 8))
    return tuple(out)


def deadlock_matches() -> list[dict[str, Any]]:
    import copy

    return copy.deepcopy(list(_deadlock_matches_cached()))
