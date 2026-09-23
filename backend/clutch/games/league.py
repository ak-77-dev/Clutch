"""League of Legends via the official Riot API.

- account-v1: Riot ID (``Name#TAG``) -> puuid
- summoner-v4 / league-v4: level, profile icon, ranked entries
- match-v5: match ids + full match JSON
- Data Dragon (no key): champion / item / profile icon images
"""

from __future__ import annotations

import os
from functools import cached_property
from typing import Any
from urllib.parse import quote

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

DDRAGON = "https://ddragon.leagueoflegends.com"
FALLBACK_PATCH = "16.18.1"

PLATFORM_TO_REGION = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "me1": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "sg2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

QUEUES = {
    420: "Ranked Solo/Duo",
    440: "Ranked Flex",
    400: "Normal Draft",
    430: "Normal Blind",
    490: "Quickplay",
    450: "ARAM",
    1700: "Arena",
    1900: "URF",
    900: "ARURF",
}
ROLES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
TIERS = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISIONS = {"IV": 0, "III": 1, "II": 2, "I": 3}

META = GameMeta(
    id="lol",
    name="League of Legends",
    character_label="Champion",
    search_hint="Riot ID, e.g. Faker#KR1",
    accent="#c89b3c",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kda", "KDA", "float2"),
        Metric("cs", "CS", "int"),
        Metric("cs_per_min", "CS / min", "float1", short="CS/m"),
        Metric("gold_per_min", "Gold / min", "int", short="GPM"),
        Metric("damage_per_min", "Damage / min", "int", short="DPM"),
        Metric("damage_share", "Damage share", "pct", short="DMG%"),
        Metric("kill_participation", "Kill participation", "pct", short="KP"),
        Metric("vision_per_min", "Vision / min", "float2", short="VS/m"),
        Metric("control_wards", "Control wards", "float1", short="CW"),
    ),
    kpis=("kda", "cs_per_min", "kill_participation", "damage_per_min", "vision_per_min"),
    trend_metrics=("kda", "cs_per_min", "damage_per_min"),
    factor_metrics=("cs_per_min", "vision_per_min", "deaths", "kill_participation", "damage_share", "control_wards"),
    card_metrics=("kda", "cs_per_min", "kill_participation", "damage_per_min"),
)


def rank_value(tier: str | None, division: str | None, lp: int | None = 0) -> float | None:
    if not tier or tier.upper() not in TIERS:
        return None
    t = TIERS.index(tier.upper())
    if t >= TIERS.index("MASTER"):  # apex tiers have no divisions; LP is uncapped
        return TIERS.index("MASTER") * 4 + (t - TIERS.index("MASTER")) * 4 + min(lp or 0, 1500) / 500
    return t * 4 + DIVISIONS.get((division or "IV").upper(), 0) + min(lp or 0, 100) / 100


def rank_label(tier: str | None, division: str | None) -> str | None:
    if not tier:
        return None
    name = tier.capitalize()
    return name if tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER") else f"{name} {division}"


class LeagueProvider(GameProvider):
    meta = META

    def __init__(self, api_key: str | None = None, platform: str | None = None, client: JsonClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("RIOT_API_KEY")
        self.platform = (platform or os.environ.get("LOL_PLATFORM") or "na1").lower()
        self.region = PLATFORM_TO_REGION.get(self.platform, "americas")
        # Riot dev keys: 20 req / 1 s and 100 req / 2 min.
        self.client = client or JsonClient(
            headers={"X-Riot-Token": self.api_key or ""},
            limiter=SlidingWindowLimiter([(20, 1.0), (100, 120.0)]),
        )

    def configured(self) -> bool:
        return bool(self.api_key)

    # ── assets ───────────────────────────────────────────────────────────────
    @cached_property
    def patch(self) -> str:
        try:
            return JsonClient(timeout=5, max_retries=0).get(f"{DDRAGON}/api/versions.json")[0]
        except Exception:  # offline / blocked: icons fall back to a known patch
            return FALLBACK_PATCH

    def champion_icon(self, champion: str) -> str:
        return f"{DDRAGON}/cdn/{self.patch}/img/champion/{champion}.png"

    def item_icon(self, item_id: int) -> str:
        return f"{DDRAGON}/cdn/{self.patch}/img/item/{item_id}.png"

    def profile_icon(self, icon_id: int | None) -> str | None:
        return f"{DDRAGON}/cdn/{self.patch}/img/profileicon/{icon_id}.png" if icon_id is not None else None

    # ── live API ─────────────────────────────────────────────────────────────
    def _regional(self, path: str) -> Any:
        return self.client.get(f"https://{self.region}.api.riotgames.com{path}")

    def _platform(self, path: str) -> Any:
        return self.client.get(f"https://{self.platform}.api.riotgames.com{path}")

    def resolve(self, query: str) -> Profile:
        if "#" not in query:
            raise NotFound("Use a full Riot ID: Name#TAG")
        name, tag = (s.strip() for s in query.rsplit("#", 1))
        acct = self._regional(f"/riot/account/v1/accounts/by-riot-id/{quote(name)}/{quote(tag)}")
        profile = Profile(
            game="lol", key=acct["puuid"], name=acct.get("gameName") or name, tag=acct.get("tagLine") or tag, region=self.platform
        )
        return self.refresh_profile(profile)

    def refresh_profile(self, profile: Profile) -> Profile:
        summoner = self._platform(f"/lol/summoner/v4/summoners/by-puuid/{profile.key}")
        entries = self._platform(f"/lol/league/v4/entries/by-puuid/{profile.key}")
        profile.icon = self.profile_icon(summoner.get("profileIconId"))
        profile.level = summoner.get("summonerLevel")
        profile.ranks = [self._rank_entry(e) for e in entries if e.get("queueType") in ("RANKED_SOLO_5x5", "RANKED_FLEX_SR")]
        return profile

    @staticmethod
    def _rank_entry(e: dict[str, Any]) -> dict[str, Any]:
        queue = "Ranked Solo/Duo" if e.get("queueType") == "RANKED_SOLO_5x5" else "Ranked Flex"
        return {
            "queue": queue,
            "label": rank_label(e.get("tier"), e.get("rank")),
            "tier": (e.get("tier") or "").lower(),
            "lp": e.get("leaguePoints"),
            "value": rank_value(e.get("tier"), e.get("rank"), e.get("leaguePoints")),
            "wins": e.get("wins"),
            "losses": e.get("losses"),
        }

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        return self._regional(f"/lol/match/v5/matches/by-puuid/{profile.key}/ids?start=0&count={min(limit, 100)}")

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return self._regional(f"/lol/match/v5/matches/{match_id}")

    # ── parsing ──────────────────────────────────────────────────────────────
    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["metadata"]["matchId"]

    def match_date(self, raw: dict[str, Any]) -> str:
        from datetime import datetime, timezone

        ms = raw["info"].get("gameStartTimestamp") or raw["info"].get("gameCreation") or 0
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        info = raw["info"]
        parts = info.get("participants", [])
        me = next((p for p in parts if p.get("puuid") == profile.key), None)
        if me is None:
            return None
        duration = float(info.get("gameDuration") or 0)
        if duration > 20_000:  # pre-11.20 payloads report milliseconds
            duration /= 1000
        minutes = max(duration / 60, 1)
        team = [p for p in parts if p.get("teamId") == me.get("teamId")]
        my_kills = sum(p.get("kills", 0) for p in team)
        team_kills = my_kills or 1
        team_damage = sum(p.get("totalDamageDealtToChampions", 0) for p in team) or 1
        k, d, a = me.get("kills", 0), me.get("deaths", 0), me.get("assists", 0)
        cs = me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)
        ch = me.get("challenges") or {}
        remake = bool(me.get("gameEndedInEarlySurrender")) or duration < 300

        metrics = {
            "kills": k,
            "deaths": d,
            "assists": a,
            "kda": round((k + a) / max(d, 1), 2),
            "cs": cs,
            "cs_per_min": round(cs / minutes, 2),
            "gold_per_min": round(me.get("goldEarned", 0) / minutes, 1),
            "damage_per_min": round(me.get("totalDamageDealtToChampions", 0) / minutes, 1),
            "damage_share": round(100 * (ch.get("teamDamagePercentage") or me.get("totalDamageDealtToChampions", 0) / team_damage), 1),
            "kill_participation": round(100 * (ch.get("killParticipation") or (k + a) / team_kills), 1),
            "vision_per_min": round(me.get("visionScore", 0) / minutes, 2),
            "control_wards": me.get("visionWardsBoughtInGame", 0),
        }
        opp_kills = sum(p.get("kills", 0) for p in parts if p.get("teamId") != me.get("teamId"))
        mates = [p for p in team if p.get("puuid") != profile.key]

        def name_of(p: dict[str, Any]) -> str:
            n = p.get("riotIdGameName") or p.get("summonerName") or "?"
            return f"{n}#{p['riotIdTagline']}" if p.get("riotIdTagline") else n

        scoreboard = []
        for p in sorted(
            parts, key=lambda p: (p.get("teamId", 0), list(ROLES).index(p["teamPosition"]) if p.get("teamPosition") in ROLES else 9)
        ):
            pcs = p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0)
            scoreboard.append(
                ScoreRow(
                    name=name_of(p),
                    team="Blue" if p.get("teamId") == 100 else "Red",
                    character=p.get("championName", "?"),
                    character_icon=self.champion_icon(p.get("championName", "")),
                    is_self=p.get("puuid") == profile.key,
                    stats={
                        "K/D/A": f"{p.get('kills', 0)}/{p.get('deaths', 0)}/{p.get('assists', 0)}",
                        "CS": pcs,
                        "Damage": p.get("totalDamageDealtToChampions", 0),
                        "Gold": p.get("goldEarned", 0),
                        "Vision": p.get("visionScore", 0),
                    },
                    extra={"items": [self.item_icon(p[f"item{i}"]) for i in range(7) if p.get(f"item{i}")], "won": bool(p.get("win"))},
                )
            )
        return Match(
            game="lol",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=QUEUES.get(info.get("queueId"), info.get("gameMode", "Custom").title()),
            duration_s=duration,
            result="remake" if remake else ("win" if me.get("win") else "loss"),
            character=me.get("championName", "?"),
            character_icon=self.champion_icon(me.get("championName", "")),
            metrics=metrics,
            role=ROLES.get(me.get("teamPosition") or ""),
            score_line=f"{my_kills}–{opp_kills}",
            teammates=[name_of(p) for p in mates],
            team_keys=[p.get("puuid", "") for p in mates],
            items=[self.item_icon(me[f"item{i}"]) for i in range(7) if me.get(f"item{i}")],
            scoreboard=scoreboard,
            link=None,
        )

    # ── demo ─────────────────────────────────────────────────────────────────
    def demo_profile(self) -> Profile:
        from clutch.games.demo_data import LOL_DEMO_PROFILE

        p = Profile(**LOL_DEMO_PROFILE)
        p.icon = self.profile_icon(29)
        # Keep the ranked record consistent with the demo match history.
        record = {420: [0, 0], 440: [0, 0]}
        for m in self.demo_matches():
            q = m["info"]["queueId"]
            me = next(x for x in m["info"]["participants"] if x["puuid"] == p.key)
            if q in record and not me["gameEndedInEarlySurrender"]:
                record[q][0 if me["win"] else 1] += 1
        for r, q in zip(p.ranks, (420, 440), strict=True):
            r["wins"], r["losses"] = record[q]
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_data import lol_matches

        return lol_matches()
