"""PUBG: Battlegrounds via the official PUBG API (https://documentation.pubg.com). Free key.

- ``/shards/{shard}/players?filter[playerNames]=`` -> account id + the last 14 days of match ids
- ``/shards/{shard}/matches/{id}`` -> the match with every participant's stats (not rate limited)
- ``/seasons`` + ``/players/{id}/seasons/{season}/ranked`` -> current ranked tier

A top-10 finish counts as a win: with 60–100 players a chicken dinner is too rare
to learn from, and placing top 10 is what PUBG's own stats call a good game. The
"character" is the map, so the pool view shows where you do well.
"""

from __future__ import annotations

import os
from typing import Any

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

API = "https://api.pubg.com"
MAPS = {
    "Baltic_Main": "Erangel",
    "Erangel_Main": "Erangel",
    "Desert_Main": "Miramar",
    "Savage_Main": "Sanhok",
    "DihorOtok_Main": "Vikendi",
    "Tiger_Main": "Taego",
    "Kiki_Main": "Deston",
    "Neon_Main": "Rondo",
    "Chimera_Main": "Paramo",
    "Heaven_Main": "Haven",
    "Summerland_Main": "Karakin",
    "Range_Main": "Camp Jackal",
}
TOP = 10

META = GameMeta(
    id="pubg",
    name="PUBG: Battlegrounds",
    character_label="Map",
    search_hint="PUBG name (case-sensitive)",
    accent="#f2a900",
    metrics=(
        Metric("placement", "Placement", "float1", higher_is_better=False, short="#"),
        Metric("kills", "Kills", "float1", short="K"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("knocks", "Knocks", "float1", short="DBNO"),
        Metric("damage", "Damage", "int", short="DMG"),
        Metric("headshot_pct", "Headshot kills", "pct", short="HS%"),
        Metric("minutes_alive", "Time alive (min)", "float1", short="ALIVE"),
        Metric("distance_km", "Distance (km)", "float1", short="KM"),
        Metric("longest_kill", "Longest kill (m)", "int", short="LONG"),
        Metric("heals", "Heals + boosts", "float1", short="HEAL"),
    ),
    kpis=("placement", "kills", "damage", "minutes_alive"),
    trend_metrics=("damage", "placement", "kills"),
    factor_metrics=("damage", "kills", "knocks", "distance_km", "heals"),  # time alive is the placement itself
    card_metrics=("placement", "kills", "damage", "minutes_alive"),
)


def mode_label(game_mode: str | None, match_type: str | None) -> str:
    """'squad-fpp' + 'competitive' -> 'Ranked Squad FPP'."""
    parts = (game_mode or "unknown").split("-")
    text = " ".join(p.upper() if p in ("fpp", "tpp") else p.title() for p in parts)
    return f"Ranked {text}" if match_type == "competitive" else text


def slim_match(doc: dict[str, Any]) -> dict[str, Any]:
    """Match JSON:API document -> participants (stats) and rosters (teams, placement)."""
    data = doc["data"]
    attrs = data.get("attributes") or {}
    participants, rosters = {}, []
    for inc in doc.get("included") or []:
        if inc.get("type") == "participant":
            participants[inc["id"]] = (inc.get("attributes") or {}).get("stats") or {}
    for inc in doc.get("included") or []:
        if inc.get("type") == "roster":
            ids = [r["id"] for r in ((inc.get("relationships") or {}).get("participants") or {}).get("data") or []]
            rosters.append(
                {
                    "rank": ((inc.get("attributes") or {}).get("stats") or {}).get("rank"),
                    "players": [participants[i] for i in ids if i in participants],
                }
            )
    keep = ("createdAt", "duration", "gameMode", "mapName", "matchType")
    return {"id": data["id"], **{k: attrs.get(k) for k in keep}, "rosters": rosters}


class PubgProvider(GameProvider):
    meta = META

    def __init__(self, api_key: str | None = None, shard: str | None = None, client: JsonClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("PUBG_API_KEY")
        self.shard = shard or os.environ.get("PUBG_SHARD") or "steam"
        headers = {"Authorization": f"Bearer {self.api_key or ''}", "Accept": "application/vnd.api+json"}
        self.client = client or JsonClient(headers=headers, limiter=SlidingWindowLimiter([(10, 60.0)]))  # 10 req/min
        self.matches = JsonClient(headers=headers) if client is None else client  # match lookups aren't rate limited
        self._recent: dict[str, list[str]] = {}
        self._season: str | None = None

    def configured(self) -> bool:
        return bool(self.api_key)

    def resolve(self, query: str) -> Profile:
        name = query.strip()
        try:
            doc = self.client.get(f"{API}/shards/{self.shard}/players", {"filter[playerNames]": name})
        except NotFound:
            raise NotFound("No PUBG player by that name (names are case-sensitive).") from None
        player = (doc.get("data") or [None])[0]
        if not player:
            raise NotFound("No PUBG player by that name (names are case-sensitive).")
        return self._profile(player)

    def _profile(self, player: dict[str, Any]) -> Profile:
        rel = ((player.get("relationships") or {}).get("matches") or {}).get("data") or []
        self._recent[player["id"]] = [m["id"] for m in rel]
        profile = Profile(
            game="pubg", key=player["id"], name=(player.get("attributes") or {}).get("name") or player["id"], region=self.shard
        )
        profile.ranks = self._ranks(player["id"])
        return profile

    def _ranks(self, account_id: str) -> list[dict[str, Any]]:
        try:
            if self._season is None:
                seasons = self.client.get(f"{API}/shards/{self.shard}/seasons").get("data") or []
                self._season = next((s["id"] for s in seasons if (s.get("attributes") or {}).get("isCurrentSeason")), "")
            if not self._season:
                return []
            ranked = self.client.get(f"{API}/shards/{self.shard}/players/{account_id}/seasons/{self._season}/ranked")
        except Exception:  # ranked stats are a bonus; the match history still works without them
            return []
        stats = ((ranked.get("data") or {}).get("attributes") or {}).get("rankedGameModeStats") or {}
        out = []
        for mode, s in stats.items():
            tier = (s.get("currentTier") or {}).get("tier")
            sub = (s.get("currentTier") or {}).get("subTier")
            label = f"{tier} {sub}" if tier and tier not in ("Master", "Survivor") else tier
            out.append(
                {
                    "queue": f"Ranked {mode_label(mode, None)}",
                    "label": label or "Unranked",
                    "tier": (tier or "unranked").lower(),
                    "lp": s.get("currentRankPoint"),
                    "value": float(s["currentRankPoint"]) if s.get("currentRankPoint") is not None else None,
                    "wins": s.get("wins"),
                    "losses": (s.get("roundsPlayed") or 0) - (s.get("wins") or 0) if s.get("roundsPlayed") is not None else None,
                }
            )
        return sorted(out, key=lambda r: -(r["value"] or 0))

    def refresh_profile(self, profile: Profile) -> Profile:
        player = self.client.get(f"{API}/shards/{self.shard}/players/{profile.key}").get("data")
        return self._profile(player)

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        if profile.key not in self._recent:
            self.refresh_profile(profile)
        return self._recent.get(profile.key, [])[:limit]  # newest first, last 14 days

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return slim_match(self.matches.get(f"{API}/shards/{self.shard}/matches/{match_id}"))

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["id"]

    def match_date(self, raw: dict[str, Any]) -> str:
        return raw.get("createdAt") or "1970-01-01T00:00:00Z"

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        rosters = raw.get("rosters") or []
        mine = next((r for r in rosters for p in r["players"] if p.get("playerId") == profile.key), None)
        if mine is None:
            return None
        me = next(p for p in mine["players"] if p.get("playerId") == profile.key)
        place = me.get("winPlace") or mine.get("rank") or len(rosters)
        kills = me.get("kills") or 0
        map_name = MAPS.get(raw.get("mapName") or "", (raw.get("mapName") or "Unknown").replace("_Main", ""))
        alive = me.get("timeSurvived") or 0
        return Match(
            game="pubg",
            id=raw["id"],
            date=self.match_date(raw),
            mode=mode_label(raw.get("gameMode"), raw.get("matchType")),
            duration_s=float(raw.get("duration") or alive),
            result="win" if place <= TOP else "loss",
            character=map_name,
            character_icon=None,
            role=None,
            map=map_name,
            metrics={
                "placement": place,
                "kills": kills,
                "assists": me.get("assists"),
                "knocks": me.get("DBNOs"),
                "damage": round(me.get("damageDealt") or 0),
                "headshot_pct": round(100 * (me.get("headshotKills") or 0) / kills, 1) if kills else None,
                "minutes_alive": round(alive / 60, 1),
                "distance_km": round(
                    ((me.get("walkDistance") or 0) + (me.get("rideDistance") or 0) + (me.get("swimDistance") or 0)) / 1000, 2
                ),
                "longest_kill": round(me.get("longestKill") or 0) if kills else None,
                "heals": (me.get("heals") or 0) + (me.get("boosts") or 0),
            },
            score_line=f"#{place} of {len(rosters)}",
            teammates=[p.get("name") or "?" for p in mine["players"] if p is not me],
            team_keys=[p.get("playerId") for p in mine["players"] if p is not me and p.get("playerId")],
            scoreboard=[
                ScoreRow(
                    name=p.get("name") or "?",
                    team=f"#{r.get('rank')}",
                    character=map_name,
                    character_icon=None,
                    is_self=p is me,
                    stats={
                        "Kills": p.get("kills") or 0,
                        "Damage": round(p.get("damageDealt") or 0),
                        "Knocks": p.get("DBNOs") or 0,
                        "Alive": f"{(p.get('timeSurvived') or 0) / 60:.0f}m",
                    },
                    extra={"won": (r.get("rank") or 99) <= TOP},
                )
                for r in sorted(rosters, key=lambda r: r.get("rank") or 999)[:10]
                for p in r["players"]
            ],
            link=None,
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import PUBG_DEMO_PROFILE

        return Profile(**PUBG_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import pubg_matches

        return pubg_matches()
