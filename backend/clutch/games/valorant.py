"""Valorant via the community HenrikDev API (https://docs.henrikdev.xyz).

Riot's official Valorant match API is limited to approved production apps,
so hobby trackers use HenrikDev (free key). Agent, map and rank art comes from
valorant-api.com (no key).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

HENRIK = "https://api.henrikdev.xyz/valorant"
MEDIA = "https://media.valorant-api.com"
TIERS_UUID = "03621f52-342b-cf4e-4f86-9350a49c6d04"  # current competitive tier set

AGENTS: dict[str, tuple[str, str]] = {  # name -> (uuid, role)
    "Jett": ("add6443a-41bd-e414-f6ad-e58d267f4e95", "Duelist"),
    "Reyna": ("a3bfb853-43b2-7238-a4f1-ad90e9e46bcc", "Duelist"),
    "Raze": ("f94c3b30-42be-e959-889c-5aa313dba261", "Duelist"),
    "Phoenix": ("eb93336a-449b-9c1b-0a54-a891f7921d69", "Duelist"),
    "Neon": ("bb2a4828-46eb-8cd1-e765-15848195d751", "Duelist"),
    "Yoru": ("7f94d92c-4234-0a36-9646-3a87eb8b5c89", "Duelist"),
    "Iso": ("0e38b510-41a8-5780-5e8f-568b2a4f2d6c", "Duelist"),
    "Waylay": ("df1cb487-4902-002e-5c17-d28e83e78588", "Duelist"),
    "Sova": ("320b2a48-4d9b-a075-30f1-1f93a9b638fa", "Initiator"),
    "Skye": ("6f2a04ca-43e0-be17-7f36-b3908627744d", "Initiator"),
    "Fade": ("dade69b4-4f5a-8528-247b-219e5a1facd6", "Initiator"),
    "Breach": ("5f8d3a7f-467b-97f3-062c-13acf203c006", "Initiator"),
    "KAY/O": ("601dbbe7-43ce-be57-2a40-4abd24953621", "Initiator"),
    "Gekko": ("e370fa57-4757-3604-3648-499e1f642d3f", "Initiator"),
    "Tejo": ("b444168c-4e35-8076-db47-ef9bf368f384", "Initiator"),
    "Omen": ("8e253930-4c05-31dd-1b6c-968525494517", "Controller"),
    "Brimstone": ("9f0d8ba9-4140-b941-57d3-a7ad57c6b417", "Controller"),
    "Viper": ("707eab51-4836-f488-046a-cda6bf494859", "Controller"),
    "Astra": ("41fb69c1-4189-7b37-f117-bcaf1e96f1bf", "Controller"),
    "Harbor": ("95b78ed7-4637-86d9-7e41-71ba8c293152", "Controller"),
    "Clove": ("1dbf2edd-4729-0984-3115-daa5eed44993", "Controller"),
    "Miks": ("7c8a4701-4de6-9355-b254-e09bc2a34b72", "Controller"),
    "Killjoy": ("1e58de9c-4950-5125-93e9-a0aee9f98746", "Sentinel"),
    "Cypher": ("117ed9e3-49f3-6512-3ccf-0cada7e3823b", "Sentinel"),
    "Sage": ("569fdd95-4d10-43ab-ca70-79becc718b46", "Sentinel"),
    "Chamber": ("22697a3d-45bf-8dd7-4fec-84a9e28c69d7", "Sentinel"),
    "Deadlock": ("cc8b64c8-4b25-4ff9-6e7f-37b4da43d235", "Sentinel"),
    "Vyse": ("efba5359-4016-a1e5-7626-b1ae76895940", "Sentinel"),
    "Veto": ("92eeef5d-43b5-1d4a-8d03-b3927a09034b", "Sentinel"),
}
MAPS: dict[str, str] = {
    "Ascent": "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319",
    "Split": "d960549e-485c-e861-8d71-aa9d1aed12a2",
    "Fracture": "b529448b-4d60-346e-e89e-00a4c527a405",
    "Bind": "2c9d57ec-4431-9c5e-2939-8f9ef6dd5cba",
    "Breeze": "2fb9a4fd-47b8-4e7d-a969-74b4046ebd53",
    "Abyss": "224b0a95-48b9-f703-1bd8-67aca101a61f",
    "Lotus": "2fe4ed3a-450a-948b-6d6b-e89a78e680a9",
    "Sunset": "92584fbe-486a-b1b2-9faa-39b0f486b498",
    "Pearl": "fd267378-4d1d-484f-ff52-77821ed10dc2",
    "Icebox": "e2ad5c54-4114-a870-9641-8ea21279579a",
    "Haven": "2bee0dc9-4ffe-519b-1cbd-7fbe763a6047",
    "Corrode": "1c18ab1f-420d-0d8b-71d0-77ad3c439115",
}
# Played to a kill target, not rounds: per-round stats (ACS, ADR) are meaningless and would skew averages.
NON_ROUND_MODES = {"Deathmatch", "Team Deathmatch"}
TIER_NAMES = [
    "Unranked",
    "Unused1",
    "Unused2",
    "Iron 1",
    "Iron 2",
    "Iron 3",
    "Bronze 1",
    "Bronze 2",
    "Bronze 3",
    "Silver 1",
    "Silver 2",
    "Silver 3",
    "Gold 1",
    "Gold 2",
    "Gold 3",
    "Platinum 1",
    "Platinum 2",
    "Platinum 3",
    "Diamond 1",
    "Diamond 2",
    "Diamond 3",
    "Ascendant 1",
    "Ascendant 2",
    "Ascendant 3",
    "Immortal 1",
    "Immortal 2",
    "Immortal 3",
    "Radiant",
]

META = GameMeta(
    id="valorant",
    name="Valorant",
    character_label="Agent",
    search_hint="Riot ID, e.g. TenZ#0505",
    accent="#ff4655",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kd", "K/D", "float2"),
        Metric("acs", "Combat score", "int", short="ACS"),
        Metric("adr", "Damage / round", "int", short="ADR"),
        Metric("hs_pct", "Headshot %", "pct", short="HS%"),
        Metric("deaths_per_round", "Deaths / round", "float2", higher_is_better=False, short="DPR"),
        Metric("assists_per_round", "Assists / round", "float2", short="APR"),
    ),
    kpis=("kd", "acs", "adr", "hs_pct"),
    trend_metrics=("acs", "kd", "hs_pct"),
    factor_metrics=("hs_pct", "adr", "deaths_per_round", "assists_per_round"),
    card_metrics=("kd", "acs", "adr", "hs_pct"),
)


def agent_icon(name: str) -> str | None:
    a = AGENTS.get(name)
    return f"{MEDIA}/agents/{a[0]}/displayicon.png" if a else None


def tier_icon(tier: int | None) -> str | None:
    return f"{MEDIA}/competitivetiers/{TIERS_UUID}/{tier}/smallicon.png" if tier else None


def tier_name(tier: int | None) -> str | None:
    return TIER_NAMES[tier] if tier and 3 <= tier < len(TIER_NAMES) else None


class ValorantProvider(GameProvider):
    meta = META

    def __init__(self, api_key: str | None = None, client: JsonClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("HENRIK_API_KEY")
        # Free HenrikDev keys: 30 requests / minute.
        self.client = client or JsonClient(
            headers={"Authorization": self.api_key or ""},
            limiter=SlidingWindowLimiter([(30, 60.0)]),
        )
        self._page: dict[str, dict[str, Any]] = {}

    def configured(self) -> bool:
        return bool(self.api_key)

    def resolve(self, query: str) -> Profile:
        if "#" not in query:
            raise NotFound("Use a full Riot ID: Name#TAG")
        name, tag = (s.strip() for s in query.rsplit("#", 1))
        acct = self.client.get(f"{HENRIK}/v1/account/{quote(name)}/{quote(tag)}")["data"]
        profile = Profile(
            game="valorant",
            key=acct["puuid"],
            name=acct.get("name") or name,
            tag=acct.get("tag") or tag,
            region=acct.get("region", "na"),
            level=acct.get("account_level"),
            icon=(acct.get("card") or {}).get("small"),
        )
        return self.refresh_profile(profile)

    def refresh_profile(self, profile: Profile) -> Profile:
        data = self.client.get(f"{HENRIK}/v2/by-puuid/mmr/{profile.region}/{profile.key}")["data"]
        cur = data.get("current_data") or {}
        tier = cur.get("currenttier")
        profile.ranks = [
            {
                "queue": "Competitive",
                "label": tier_name(tier) or "Unranked",
                "tier": (tier_name(tier) or "unranked").split()[0].lower(),
                "lp": cur.get("ranking_in_tier"),
                "value": tier,
                "icon": tier_icon(tier),
                "wins": None,
                "losses": None,
            }
        ]
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        # v3 returns full match objects; keep them so fetch_match needs no extra call.
        data = self.client.get(f"{HENRIK}/v3/by-puuid/matches/{profile.region}/{profile.key}", {"size": min(limit, 10)})["data"]
        ids = []
        for m in data:
            if not (m or {}).get("metadata"):  # HenrikDev stubs out unavailable matches with metadata=None
                continue
            mid = m["metadata"]["matchid"]
            self._page[mid] = m
            ids.append(mid)
        return ids

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return self._page.pop(match_id, None) or self.client.get(f"{HENRIK}/v2/match/{match_id}")["data"]

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["metadata"]["matchid"]

    def match_date(self, raw: dict[str, Any]) -> str:
        start = raw["metadata"].get("game_start") or 0
        return datetime.fromtimestamp(start, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        md = raw["metadata"]
        if md.get("mode") in NON_ROUND_MODES:
            return None
        players = (raw.get("players") or {}).get("all_players") or []
        me = next((p for p in players if p.get("puuid") == profile.key), None)
        if me is None:
            return None
        rounds = max(int(md.get("rounds_played") or 0), 1)
        st = me.get("stats") or {}
        k, d, a = st.get("kills", 0), st.get("deaths", 0), st.get("assists", 0)
        shots = (st.get("headshots", 0) + st.get("bodyshots", 0) + st.get("legshots", 0)) or 1
        team = (me.get("team") or "").lower()
        teams = raw.get("teams") or {}
        mine = teams.get(team) or {}
        other = teams.get("blue" if team == "red" else "red") or {}
        won_r, lost_r = mine.get("rounds_won", 0), mine.get("rounds_lost", other.get("rounds_won", 0))
        if mine.get("has_won"):
            result = "win"
        elif other.get("has_won"):
            result = "loss"
        else:
            result = "draw" if won_r == lost_r and rounds > 1 else "loss"
        length = float(md.get("game_length") or 0)
        if length > 20_000:  # milliseconds in some API versions
            length /= 1000
        tier = me.get("currenttier")
        mates = [p for p in players if (p.get("team") or "").lower() == team and p.get("puuid") != profile.key]

        scoreboard = [
            ScoreRow(
                name=f"{p.get('name')}#{p.get('tag')}",
                team=(p.get("team") or "?").title(),
                character=p.get("character", "?"),
                character_icon=((p.get("assets") or {}).get("agent") or {}).get("small") or agent_icon(p.get("character", "")),
                is_self=p.get("puuid") == profile.key,
                rank=p.get("currenttier_patched"),
                extra={"won": bool((teams.get((p.get("team") or "").lower()) or {}).get("has_won"))},
                stats={
                    "ACS": round((p.get("stats") or {}).get("score", 0) / rounds),
                    "K/D/A": f"{(p.get('stats') or {}).get('kills', 0)}/{(p.get('stats') or {}).get('deaths', 0)}/{(p.get('stats') or {}).get('assists', 0)}",
                    "ADR": round((p.get("damage_made") or 0) / rounds),
                },
            )
            for p in sorted(players, key=lambda p: (p.get("team", ""), -(p.get("stats") or {}).get("score", 0)))
        ]
        return Match(
            game="valorant",
            id=md["matchid"],
            date=self.match_date(raw),
            mode=md.get("mode") or "Unknown",
            duration_s=length,
            result=result,
            character=me.get("character", "?"),
            character_icon=((me.get("assets") or {}).get("agent") or {}).get("small") or agent_icon(me.get("character", "")),
            role=AGENTS.get(me.get("character", ""), (None, None))[1],
            map=md.get("map"),
            metrics={
                "kills": k,
                "deaths": d,
                "assists": a,
                "kd": round(k / max(d, 1), 2),
                "acs": round(st.get("score", 0) / rounds, 1),
                "adr": round((me.get("damage_made") or 0) / rounds, 1),
                "hs_pct": round(100 * st.get("headshots", 0) / shots, 1),
                "deaths_per_round": round(d / rounds, 3),
                "assists_per_round": round(a / rounds, 3),
            },
            rank_label=tier_name(tier) if md.get("mode") == "Competitive" else None,
            rank_value=float(tier) if md.get("mode") == "Competitive" and tier else None,
            score_line=f"{won_r}–{lost_r}",
            teammates=[f"{p.get('name')}#{p.get('tag')}" for p in mates],
            team_keys=[p.get("puuid", "") for p in mates],
            scoreboard=scoreboard,
            link=f"https://tracker.gg/valorant/match/{md['matchid']}",
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_data import VAL_DEMO_PROFILE

        p = Profile(**VAL_DEMO_PROFILE)
        # Current rank = tier in the latest competitive demo match, so profile and history agree.
        comp = [m for m in self.demo_matches() if m["metadata"]["mode"] == "Competitive"]
        me = next(x for x in comp[-1]["players"]["all_players"] if x["puuid"] == p.key)
        tier = me["currenttier"]
        p.ranks = [
            {
                "queue": "Competitive",
                "label": tier_name(tier),
                "tier": (tier_name(tier) or "").split()[0].lower(),
                "lp": None,
                "value": tier,
                "icon": tier_icon(tier),
                "wins": None,
                "losses": None,
            }
        ]
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_data import val_matches

        return val_matches()
