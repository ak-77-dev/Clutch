"""Teamfight Tactics via the official Riot API (the same key as League of Legends).

- account-v1: Riot ID -> puuid (shared with League)
- tft-summoner-v1 / tft-league-v1: level, icon, Ranked TFT tier
- tft-match-v1: match ids + full match JSON

A top-4 finish counts as a win (it gains LP). The "character" is the comp: the
strongest trait you ran, so the pool view shows which comps place well for you.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from clutch.games.league import LeagueProvider, rank_label, rank_value
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

QUEUES = {
    1100: "Ranked",
    1090: "Normal",
    1130: "Hyper Roll",
    1160: "Double Up",
    1110: "Tutorial",
    6000: "Revival",
    1210: "Choncc's Treasure",
}

META = GameMeta(
    id="tft",
    name="Teamfight Tactics",
    character_label="Comp",
    search_hint="Riot ID, e.g. Dishsoap#NA1",
    accent="#9b6dff",
    metrics=(
        Metric("placement", "Placement", "float1", higher_is_better=False, short="#"),
        Metric("top4", "Top 4", "pct", short="T4"),
        Metric("damage", "Damage to players", "int", short="DMG"),
        Metric("level", "Level", "float1", short="LVL"),
        Metric("gold_left", "Gold left", "float1", higher_is_better=False, short="G"),
        Metric("eliminated", "Players eliminated", "float1", short="ELIM"),
        Metric("last_round", "Rounds survived", "float1", short="RND"),
        Metric("units", "Units on board", "float1", short="UNITS"),
    ),
    kpis=("placement", "top4", "damage", "level"),
    trend_metrics=("placement", "damage"),
    factor_metrics=("level", "gold_left", "units"),  # rounds survived / damage dealt are the placement itself
    card_metrics=("placement", "damage", "level", "gold_left"),
)


def trait_name(api_name: str | None) -> str:
    """'TFT15_StarGuardian' -> 'Star Guardian'; 'Set9_Noxus' -> 'Noxus'."""
    if not api_name:
        return "No comp"
    base = api_name.split("_", 1)[1] if "_" in api_name else api_name
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip() or api_name


def main_trait(traits: list[dict[str, Any]]) -> str:
    active = [t for t in traits if (t.get("tier_current") or 0) > 0]
    if not active:
        return "No comp"
    best = max(active, key=lambda t: (t.get("style") or 0, t.get("num_units") or 0))
    return trait_name(best.get("name"))


def unit_name(character_id: str | None) -> str:
    return trait_name(character_id)  # same 'TFT15_Name' scheme


class TftProvider(LeagueProvider):
    """Shares League's Riot client (key, region routing, rate limits) and rank math."""

    meta = META

    def resolve(self, query: str) -> Profile:
        profile = super().resolve(query)
        profile.game = "tft"
        return profile

    def refresh_profile(self, profile: Profile) -> Profile:
        summoner = self._platform(f"/tft/summoner/v1/summoners/by-puuid/{profile.key}")
        entries = self._platform(f"/tft/league/v1/by-puuid/{profile.key}")
        profile.icon = self.profile_icon(summoner.get("profileIconId"))
        profile.level = summoner.get("summonerLevel")
        profile.ranks = [
            {
                "queue": "Ranked TFT" if e.get("queueType") == "RANKED_TFT" else "Double Up",
                "label": rank_label(e.get("tier"), e.get("rank")),
                "tier": (e.get("tier") or "").lower(),
                "lp": e.get("leaguePoints"),
                "value": rank_value(e.get("tier"), e.get("rank"), e.get("leaguePoints")),
                "wins": e.get("wins"),
                "losses": e.get("losses"),
            }
            for e in entries
            if e.get("queueType") in ("RANKED_TFT", "RANKED_TFT_DOUBLE_UP")
        ]
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        return self._regional(f"/tft/match/v1/matches/by-puuid/{profile.key}/ids?start=0&count={min(limit, 100)}")

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return self._regional(f"/tft/match/v1/matches/{match_id}")

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["metadata"]["match_id"]

    def match_date(self, raw: dict[str, Any]) -> str:
        ms = raw["info"].get("game_datetime") or 0
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        info = raw["info"]
        parts = info.get("participants") or []
        me = next((p for p in parts if p.get("puuid") == profile.key), None)
        if me is None:
            return None
        place = me.get("placement") or 8
        queue = info.get("queue_id") or info.get("queueId")
        double_up = queue == 1160
        top = place <= 4  # Double Up reports 1-8 per player too (team place = ceil(place / 2))

        def name_of(p: dict[str, Any]) -> str:
            n = p.get("riotIdGameName") or (p.get("puuid") or "?")[:8]
            return f"{n}#{p['riotIdTagline']}" if p.get("riotIdTagline") else n

        return Match(
            game="tft",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=QUEUES.get(queue, info.get("tft_game_type", "Custom").replace("_", " ").title()),
            duration_s=float(info.get("game_length") or me.get("time_eliminated") or 0),
            result="win" if top else "loss",
            character=main_trait(me.get("traits") or []),
            character_icon=None,
            role=f"Set {info.get('tft_set_number')}" if info.get("tft_set_number") else None,
            metrics={
                "placement": place,
                "top4": 100.0 if top else 0.0,
                "damage": me.get("total_damage_to_players"),
                "level": me.get("level"),
                "gold_left": me.get("gold_left"),
                "eliminated": me.get("players_eliminated"),
                "last_round": me.get("last_round"),
                "units": len(me.get("units") or []),
            },
            score_line=f"#{place}",
            teammates=[name_of(p) for p in parts if double_up and p is not me and p.get("partner_group_id") == me.get("partner_group_id")],
            scoreboard=[
                ScoreRow(
                    name=name_of(p),
                    team=f"#{p.get('placement')}",
                    character=main_trait(p.get("traits") or []),
                    character_icon=None,
                    is_self=p is me,
                    stats={
                        "Place": p.get("placement"),
                        "Level": p.get("level"),
                        "Damage": p.get("total_damage_to_players") or 0,
                        "Board": ", ".join(unit_name(u.get("character_id")) for u in (p.get("units") or [])[:6]),
                    },
                    extra={"won": (p.get("placement") or 8) <= 4},
                )
                for p in sorted(parts, key=lambda p: p.get("placement") or 9)
            ],
            link=None,
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import TFT_DEMO_PROFILE

        p = Profile(**TFT_DEMO_PROFILE)
        p.icon = self.profile_icon(4568)
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import tft_matches

        return tft_matches()
