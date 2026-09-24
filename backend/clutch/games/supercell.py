"""Brawl Stars and Clash Royale via Supercell's official APIs. Free keys.

- https://developer.brawlstars.com and https://developer.clashroyale.com
- Keys are locked to the IP addresses you list when you create them, so make the
  key for your home IP (https://api.ipify.org shows it).
- ``/players/%23TAG`` for the profile, ``/players/%23TAG/battlelog`` for the last 25
  battles. Battles have no id, so one is built from the battle time and the players.
  The battle log is short, so syncing often (Clutch does after every session) is
  what builds a long history.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

BRAWLIFY = "https://cdn.brawlify.com"


def normalize_tag(query: str) -> str:
    tag = query.strip().upper().lstrip("#").replace("O", "0")  # tags never contain the letter O
    if not re.fullmatch(r"[0289PYLQGRJCUV]{3,12}", tag):
        raise NotFound("Player tags look like #2PP0JC (0 2 8 9 P Y L Q G R J C U V)")
    return f"#{tag}"


def battle_time(s: str | None) -> str:
    """'20260921T193012.000Z' -> ISO 8601."""
    if not s:
        return "1970-01-01T00:00:00Z"
    return datetime.strptime(s[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def battle_id(b: dict[str, Any], *tags: str) -> str:
    return b.get("battleTime", "") + "-" + hashlib.sha1("|".join(sorted(tags)).encode()).hexdigest()[:10]


def title(camel: str | None) -> str:
    """'brawlBall' / 'PathOfLegend' -> 'Brawl Ball' / 'Path Of Legend'."""
    if not camel:
        return "Unknown"
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", camel.replace("_", " ")).strip().title()


class _Supercell(GameProvider):
    api: str
    env_key: str
    game_id: str

    def __init__(self, api_key: str | None = None, client: JsonClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get(self.env_key)
        self.client = client or JsonClient(
            headers={"Authorization": f"Bearer {self.api_key or ''}"}, limiter=SlidingWindowLimiter([(10, 1.0)])
        )
        self._log: dict[str, list[dict[str, Any]]] = {}

    def configured(self) -> bool:
        return bool(self.api_key)

    def _player(self, tag: str) -> dict[str, Any]:
        return self.client.get(f"{self.api}/players/{quote(tag)}")

    def resolve(self, query: str) -> Profile:
        tag = normalize_tag(query)
        try:
            return self._profile(self._player(tag))
        except NotFound:
            raise NotFound(f"No player with tag {tag}.") from None

    def refresh_profile(self, profile: Profile) -> Profile:
        return self._profile(self._player(profile.key))

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        items = (self.client.get(f"{self.api}/players/{quote(profile.key)}/battlelog") or {}).get("items") or []
        ids = []
        for b in items:
            b = self._with_id(b)
            self._log.setdefault(profile.key, [])
            self._log[profile.key] = [x for x in self._log[profile.key] if x["id"] != b["id"]] + [b]
            ids.append(b["id"])
        return ids[:limit]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        for b in self._log.get(profile.key, []):
            if b["id"] == match_id:
                return b
        raise NotFound("That battle has dropped out of the 25-battle log.")

    def _with_id(self, b: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _profile(self, p: dict[str, Any]) -> Profile:
        raise NotImplementedError

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["id"]

    def match_date(self, raw: dict[str, Any]) -> str:
        return battle_time(raw.get("battleTime"))


# ── Brawl Stars ──────────────────────────────────────────────────────────────

BRAWL_META = GameMeta(
    id="brawlstars",
    name="Brawl Stars",
    character_label="Brawler",
    search_hint="Player tag, e.g. #2PP0JC",
    accent="#ffc300",
    metrics=(
        Metric("trophy_change", "Trophy change", "int", short="±🏆"),
        Metric("star_player", "Star player", "pct", short="★"),
        Metric("brawler_trophies", "Brawler trophies", "int", short="🏆"),
        Metric("power", "Brawler power", "float1", short="PWR"),
        Metric("minutes", "Battle length (min)", "float1", short="MIN"),
        Metric("showdown_rank", "Showdown rank", "float1", higher_is_better=False, short="#"),
    ),
    kpis=("trophy_change", "star_player", "brawler_trophies"),
    trend_metrics=("trophy_change", "brawler_trophies"),
    factor_metrics=("power", "brawler_trophies", "minutes"),
    card_metrics=("trophy_change", "star_player", "power"),
)
SHOWDOWN_TOP = {"soloShowdown": 4, "duoShowdown": 2, "trioShowdown": 2}


class BrawlStarsProvider(_Supercell):
    meta = BRAWL_META
    api = "https://api.brawlstars.com/v1"
    env_key = "BRAWLSTARS_API_KEY"
    game_id = "brawlstars"

    def _profile(self, p: dict[str, Any]) -> Profile:
        return Profile(
            game="brawlstars",
            key=p["tag"],
            name=p.get("name") or p["tag"],
            tag=p["tag"].lstrip("#"),
            icon=f"{BRAWLIFY}/profile-icons/regular/{p['icon']['id']}.png" if (p.get("icon") or {}).get("id") else None,
            level=p.get("expLevel"),
            region=(p.get("club") or {}).get("name"),
            ranks=[
                {
                    "queue": "Trophies",
                    "label": f"{p.get('trophies', 0):,} 🏆",
                    "tier": "trophies",
                    "lp": p.get("highestTrophies"),
                    "value": float(p.get("trophies") or 0),
                    "wins": p.get("3vs3Victories"),
                    "losses": None,
                }
            ],
        )

    def _with_id(self, b: dict[str, Any]) -> dict[str, Any]:
        battle = b.get("battle") or {}
        people = [x for team in battle.get("teams") or [] for x in team] + (battle.get("players") or [])
        return {**b, "id": battle_id(b, *(x.get("tag", "") for x in people))}

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        battle = raw.get("battle") or {}
        event = raw.get("event") or {}
        teams = battle.get("teams") or ([[p] for p in battle.get("players") or []])
        mine = next((t for t in teams for p in t if p.get("tag") == profile.key), None)
        if mine is None:
            return None
        me = next(p for p in mine if p.get("tag") == profile.key)
        mode = event.get("mode") or battle.get("mode") or ""
        brawler = me.get("brawler") or (me.get("brawlers") or [{}])[0]
        rank = battle.get("rank")
        if battle.get("result"):
            result = {"victory": "win", "defeat": "loss", "draw": "draw"}.get(battle["result"], "loss")
        elif rank is not None:
            result = "win" if rank <= SHOWDOWN_TOP.get(mode, 4) else "loss"
        else:
            return None  # e.g. boss fights / challenges without a result
        star = battle.get("starPlayer") or {}
        return Match(
            game="brawlstars",
            id=raw["id"],
            date=self.match_date(raw),
            mode=title(mode) + (" (ranked)" if battle.get("type") in ("ranked", "soloRanked", "teamRanked") else ""),
            duration_s=float(battle.get("duration") or 0),
            result=result,
            character=title(brawler.get("name", "?").lower()),
            character_icon=f"{BRAWLIFY}/brawlers/borderless/{brawler['id']}.png" if brawler.get("id") else None,
            map=event.get("map"),
            metrics={
                "trophy_change": battle.get("trophyChange"),
                "star_player": 100.0 if star.get("tag") == profile.key else 0.0 if star else None,
                "brawler_trophies": brawler.get("trophies"),
                "power": brawler.get("power"),
                "minutes": round((battle.get("duration") or 0) / 60, 2) or None,
                "showdown_rank": rank,
            },
            score_line=f"#{rank}" if rank is not None else {"win": "Victory", "loss": "Defeat", "draw": "Draw"}[result],
            teammates=[p.get("name") or "?" for p in mine if p is not me],
            team_keys=[p["tag"] for p in mine if p is not me and p.get("tag")],
            scoreboard=[
                ScoreRow(
                    name=p.get("name") or "?",
                    team="Your team" if t is mine else f"Team {i + 1}",
                    character=title((p.get("brawler") or {}).get("name", "?").lower()),
                    character_icon=f"{BRAWLIFY}/brawlers/borderless/{p['brawler']['id']}.png"
                    if (p.get("brawler") or {}).get("id")
                    else None,
                    is_self=p is me,
                    stats={"Power": (p.get("brawler") or {}).get("power") or 0, "Trophies": (p.get("brawler") or {}).get("trophies") or 0},
                    extra={"won": (t is mine) == (result == "win")},
                )
                for i, t in enumerate(teams)
                for p in t
            ],
            link=None,
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import BRAWL_DEMO_PROFILE

        return Profile(**BRAWL_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import brawl_matches

        return brawl_matches()


# ── Clash Royale ─────────────────────────────────────────────────────────────

ROYALE_META = GameMeta(
    id="clashroyale",
    name="Clash Royale",
    character_label="Win condition",
    search_hint="Player tag, e.g. #2PP0JC",
    accent="#3d8bff",
    metrics=(
        Metric("crowns", "Crowns", "float1", short="👑"),
        Metric("crowns_against", "Crowns lost", "float1", higher_is_better=False, short="👑−"),
        Metric("trophy_change", "Trophy change", "int", short="±🏆"),
        Metric("elixir_leaked", "Elixir leaked", "float1", higher_is_better=False, short="LEAK"),
        Metric("deck_elixir", "Deck avg. elixir", "float2", short="ELIX"),
        Metric("level_edge", "Card level vs opponent", "float2", short="LVL±"),
    ),
    kpis=("crowns", "trophy_change", "elixir_leaked", "level_edge"),
    trend_metrics=("trophy_change", "crowns"),
    factor_metrics=("elixir_leaked", "deck_elixir", "level_edge"),
    card_metrics=("crowns", "trophy_change", "elixir_leaked"),
)
ROYALE_MODES = {
    "pathOfLegend": "Path of Legend",
    "PvP": "Ladder",
    "riverRacePvP": "River Race",
    "riverRaceDuel": "River Race Duel",
    "challenge": "Challenge",
    "tournament": "Tournament",
    "friendly": "Friendly",
}
WIN_CONDITIONS = (
    "Hog Rider", "Golem", "Giant", "Royal Giant", "Electro Giant", "Goblin Giant", "Lava Hound", "Balloon", "X-Bow", "Mortar",
    "Miner", "Graveyard", "Goblin Barrel", "Goblin Drill", "Ram Rider", "Battle Ram", "Royal Hogs", "Elixir Golem",
    "Wall Breakers", "Three Musketeers", "Sparky", "P.E.K.K.A", "Mega Knight", "Skeleton Barrel", "Giant Skeleton", "Royal Recruits",
)  # fmt: skip


def win_condition(cards: list[dict[str, Any]]) -> str:
    names = [c.get("name", "") for c in cards]
    return next((w for w in WIN_CONDITIONS if w in names), names[0] if names else "Unknown deck")


def _card_level(c: dict[str, Any]) -> float:
    # Levels are reported on each rarity's own scale; maxLevel puts them on one (max = 16 as of 2025).
    return (c.get("level") or 0) + (16 - (c.get("maxLevel") or 16))


class ClashRoyaleProvider(_Supercell):
    meta = ROYALE_META
    api = "https://api.clashroyale.com/v1"
    env_key = "CLASHROYALE_API_KEY"
    game_id = "clashroyale"

    def _profile(self, p: dict[str, Any]) -> Profile:
        pol = p.get("currentPathOfLegendSeasonResult") or {}
        ranks = [
            {
                "queue": "Trophy road",
                "label": f"{p.get('trophies', 0):,} 🏆 · {(p.get('arena') or {}).get('name', '')}".rstrip(" ·"),
                "tier": "trophies",
                "lp": p.get("bestTrophies"),
                "value": float(p.get("trophies") or 0),
                "wins": p.get("wins"),
                "losses": p.get("losses"),
            }
        ]
        if pol.get("leagueNumber"):
            ranks.append(
                {
                    "queue": "Path of Legend",
                    "label": f"League {pol['leagueNumber']}" + (f" · {pol['trophies']} rating" if pol.get("trophies") else ""),
                    "tier": "path of legend",
                    "lp": pol.get("trophies"),
                    "value": float(pol["leagueNumber"] * 1000 + (pol.get("trophies") or 0)),
                    "wins": None,
                    "losses": None,
                }
            )
        return Profile(
            game="clashroyale",
            key=p["tag"],
            name=p.get("name") or p["tag"],
            tag=p["tag"].lstrip("#"),
            level=p.get("expLevel"),
            region=(p.get("clan") or {}).get("name"),
            ranks=ranks,
        )

    def _with_id(self, b: dict[str, Any]) -> dict[str, Any]:
        tags = [x.get("tag", "") for side in ("team", "opponent") for x in b.get(side) or []]
        return {**b, "id": battle_id(b, *tags)}

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        team, opp = raw.get("team") or [], raw.get("opponent") or []
        me = next((p for p in team if p.get("tag") == profile.key), None)
        if me is None:
            team, opp = opp, team
            me = next((p for p in team if p.get("tag") == profile.key), None)
        if me is None or not opp:
            return None
        mine, theirs = me.get("crowns") or 0, max(p.get("crowns") or 0 for p in opp)
        result = "win" if mine > theirs else "loss" if mine < theirs else "draw"
        cards = me.get("cards") or []
        opp_cards = [c for p in opp for c in p.get("cards") or []]
        elixir = [c["elixirCost"] for c in cards if c.get("elixirCost") is not None]
        lvl = lambda cs: sum(_card_level(c) for c in cs) / len(cs) if cs else None  # noqa: E731
        edge = (lvl(cards) - lvl(opp_cards)) if cards and opp_cards else None
        wc = win_condition(cards)
        icon = next((((c.get("iconUrls") or {}).get("medium")) for c in cards if c.get("name") == wc), None)
        return Match(
            game="clashroyale",
            id=raw["id"],
            date=self.match_date(raw),
            mode=ROYALE_MODES.get(raw.get("type") or "", title((raw.get("gameMode") or {}).get("name") or raw.get("type"))),
            duration_s=0.0,  # the battle log doesn't say how long a battle took
            result=result,
            character=wc,
            character_icon=icon,
            map=(raw.get("arena") or {}).get("name"),
            metrics={
                "crowns": mine,
                "crowns_against": theirs,
                "trophy_change": me.get("trophyChange"),
                "elixir_leaked": me.get("elixirLeaked"),
                "deck_elixir": round(sum(elixir) / len(elixir), 2) if elixir else None,
                "level_edge": round(edge, 2) if edge is not None else None,
            },
            score_line=f"{mine}–{theirs}",
            teammates=[p.get("name") or "?" for p in team if p is not me],
            team_keys=[p["tag"] for p in team if p is not me and p.get("tag")],
            scoreboard=[
                ScoreRow(
                    name=p.get("name") or "?",
                    team="You" if side is team else "Opponent",
                    character=win_condition(p.get("cards") or []),
                    character_icon=None,
                    is_self=p is me,
                    stats={"Crowns": p.get("crowns") or 0, "Deck": ", ".join(c.get("name", "") for c in (p.get("cards") or [])[:8])},
                    extra={
                        "won": (side is team) == (result == "win"),
                        "items": [(c.get("iconUrls") or {}).get("medium") for c in p.get("cards") or []],
                    },
                )
                for side in (team, opp)
                for p in side
            ],
            items=[(c.get("iconUrls") or {}).get("medium") for c in cards if (c.get("iconUrls") or {}).get("medium")],
            link=None,
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import ROYALE_DEMO_PROFILE

        return Profile(**ROYALE_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import royale_matches

        return royale_matches()
