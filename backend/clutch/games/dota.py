"""Dota 2 via OpenDota (https://docs.opendota.com). No API key needed.

- ``/search`` and ``/players/{id}``: find a player, medal (rank tier), avatar
- ``/players/{id}/matches`` + ``/matches/{id}``: match history and full scoreboards
- Hero and item art from Valve's CDN, keyed by a bundled snapshot of OpenDota's
  constants (``scripts/snapshot_assets.py``)

Players must have "Expose Public Match Data" enabled in the Dota client, or
OpenDota only knows their profile, not their games.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from clutch.games._dota_assets import HEROES, ITEMS
from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

OPENDOTA = "https://api.opendota.com/api"
CDN = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react"
RANK_ICONS = "https://www.opendota.com/assets/images/dota2/rank_icons"
STEAM64_BASE = 76561197960265728
ANONYMOUS = 4294967295  # account_id OpenDota reports for players who hide their data

MEDALS = ["Herald", "Guardian", "Crusader", "Archon", "Legend", "Ancient", "Divine", "Immortal"]
GAME_MODES = {
    1: "All Pick",
    2: "Captains Mode",
    3: "Random Draft",
    4: "Single Draft",
    5: "All Random",
    16: "Captains Draft",
    18: "Ability Draft",
    20: "All Random Deathmatch",
    21: "1v1 Mid",
    22: "All Pick",  # "All Draft" in the API is what the client calls All Pick
    23: "Turbo",
}
RANKED_LOBBIES = {5, 6, 7}

# Top-level and per-player fields kept from /matches/{id}. OpenDota's parsed
# matches carry full combat logs (hundreds of KB); the scoreboard needs none of it.
MATCH_FIELDS = ("match_id", "start_time", "duration", "radiant_win", "game_mode", "lobby_type", "radiant_score", "dire_score", "patch")
PLAYER_FIELDS = (
    "account_id",
    "personaname",
    "player_slot",
    "isRadiant",
    "hero_id",
    "kills",
    "deaths",
    "assists",
    "last_hits",
    "denies",
    "gold_per_min",
    "xp_per_min",
    "hero_damage",
    "tower_damage",
    "hero_healing",
    "net_worth",
    "level",
    "rank_tier",
    "leaver_status",
    *(f"item_{i}" for i in range(6)),
    "item_neutral",
)

META = GameMeta(
    id="dota2",
    name="Dota 2",
    character_label="Hero",
    search_hint="Steam name or Dota friend ID",
    accent="#e03e2d",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kda", "KDA", "float2"),
        Metric("last_hits_per_min", "Last hits / min", "float1", short="LH/m"),
        Metric("gpm", "Gold / min", "int", short="GPM"),
        Metric("xpm", "XP / min", "int", short="XPM"),
        Metric("damage_per_min", "Hero damage / min", "int", short="DPM"),
        Metric("damage_share", "Damage share", "pct", short="DMG%"),
        Metric("kill_participation", "Kill participation", "pct", short="KP"),
        Metric("tower_damage", "Tower damage", "int", short="TD"),
    ),
    kpis=("kda", "gpm", "xpm", "kill_participation", "last_hits_per_min"),
    trend_metrics=("kda", "gpm", "damage_per_min"),
    factor_metrics=("last_hits_per_min", "xpm", "deaths", "kill_participation", "tower_damage"),
    card_metrics=("kda", "gpm", "xpm", "kill_participation"),
)


def hero(hero_id: int | None) -> tuple[str, str | None, str | None]:
    """(name, icon URL, role) for a hero id; unknown ids degrade to a placeholder name."""
    h = HEROES.get(hero_id or 0)
    if h is None:
        return f"Hero {hero_id}", None, None
    return h[0], f"{CDN}/heroes/{h[1]}.png", h[2]


def item_icon(item_id: int | None) -> str | None:
    slug = ITEMS.get(item_id or 0)
    return f"{CDN}/items/{slug}.png" if slug else None


def medal(rank_tier: int | None) -> tuple[str | None, float | None]:
    """OpenDota rank_tier 11..80 (tens digit = medal, ones = stars) -> ("Legend 3", ladder value)."""
    if not rank_tier or rank_tier < 11:
        return None, None
    m, stars = divmod(rank_tier, 10)
    if not 1 <= m <= len(MEDALS):
        return None, None
    name = MEDALS[m - 1] if m == 8 or not stars else f"{MEDALS[m - 1]} {stars}"
    return name, float((m - 1) * 5 + max(stars, 1) - 1)


def account_id_from(query: str) -> int | None:
    """Dota friend ID (32-bit account id) or a SteamID64 -> account id."""
    q = query.strip().removeprefix("steam:")
    if not q.isdigit():
        return None
    n = int(q)
    return n - STEAM64_BASE if n >= STEAM64_BASE else n


class DotaProvider(GameProvider):
    meta = META

    def __init__(self, client: JsonClient | None = None) -> None:
        # Keyless OpenDota: 60 requests / minute. An optional key lifts the cap.
        self.api_key = os.environ.get("OPENDOTA_API_KEY")
        self.client = client or JsonClient(limiter=SlidingWindowLimiter([(60, 60.0)]))

    def configured(self) -> bool:
        return True  # works without a key

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.api_key:
            params = {**(params or {}), "api_key": self.api_key}
        return self.client.get(f"{OPENDOTA}{path}", params)

    # ── live API ─────────────────────────────────────────────────────────────
    def resolve(self, query: str) -> Profile:
        account_id = account_id_from(query)
        if account_id is None:
            hits = self._get("/search", {"q": query.strip()}) or []
            exact = [h for h in hits if (h.get("personaname") or "").lower() == query.strip().lower()]
            # Names aren't unique: prefer an exact match, then whoever played most recently.
            pool = exact or hits
            if not pool:
                raise NotFound("No Dota 2 player by that name. Try their friend ID (Dota client → profile).")
            account_id = max(pool, key=lambda h: h.get("last_match_time") or "")["account_id"]
        return self.refresh_profile(Profile(game="dota2", key=str(account_id), name=str(account_id)))

    def refresh_profile(self, profile: Profile) -> Profile:
        data = self._get(f"/players/{profile.key}")
        info = data.get("profile") if isinstance(data, dict) else None
        if not info:
            raise NotFound("OpenDota has no public profile for that account.")
        profile.name = info.get("personaname") or profile.name
        profile.icon = info.get("avatarmedium") or info.get("avatar")
        label, value = medal(data.get("rank_tier"))
        m, stars = divmod(data.get("rank_tier") or 0, 10)
        profile.ranks = [
            {
                "queue": "Ranked",
                "label": label or "Uncalibrated",
                "tier": (label or "unranked").split()[0].lower(),
                "lp": data.get("leaderboard_rank"),
                "value": value,
                "icon": f"{RANK_ICONS}/rank_icon_{m}.png" if label else f"{RANK_ICONS}/rank_icon_0.png",
                "wins": None,
                "losses": None,
            }
        ]
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        # Each match is one more request against a 60/min budget, so keep the first sync quick.
        rows = self._get(f"/players/{profile.key}/matches", {"limit": min(limit, 20)}) or []
        return [str(r["match_id"]) for r in rows]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        raw = self._get(f"/matches/{match_id}")
        slim = {k: raw.get(k) for k in MATCH_FIELDS}
        slim["players"] = [{k: p.get(k) for k in PLAYER_FIELDS} for p in raw.get("players") or []]
        return slim

    # ── parsing ──────────────────────────────────────────────────────────────
    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["match_id"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return datetime.fromtimestamp(raw.get("start_time") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _radiant(p: dict[str, Any]) -> bool:
        return p["isRadiant"] if p.get("isRadiant") is not None else (p.get("player_slot") or 0) < 128

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        players = raw.get("players") or []
        me = next((p for p in players if str(p.get("account_id")) == profile.key), None)
        if me is None:
            return None
        duration = float(raw.get("duration") or 0)
        minutes = max(duration / 60, 1)
        radiant = self._radiant(me)
        team = [p for p in players if self._radiant(p) == radiant]
        team_kills = sum(p.get("kills") or 0 for p in team) or 1
        team_damage = sum(p.get("hero_damage") or 0 for p in team) or 1
        # Old matches OpenDota never parsed have no damage numbers: record them as unknown, not zero.
        dmg = me.get("hero_damage") if (me.get("hero_damage") or me.get("tower_damage")) else None
        k, d, a = me.get("kills") or 0, me.get("deaths") or 0, me.get("assists") or 0
        won = bool(raw.get("radiant_win")) == radiant
        # Games abandoned in the first minutes don't count, like LoL remakes.
        remake = duration < 480 and any((p.get("leaver_status") or 0) > 1 for p in players)

        mode = GAME_MODES.get(raw.get("game_mode"), "Custom")
        ranked = raw.get("lobby_type") in RANKED_LOBBIES
        mode = "Turbo" if mode == "Turbo" else f"{'Ranked' if ranked else 'Unranked'} {mode}"
        rank_label, rank_value = medal(me.get("rank_tier")) if ranked else (None, None)
        name, icon, role = hero(me.get("hero_id"))
        mine, theirs = (raw.get("radiant_score"), raw.get("dire_score")) if radiant else (raw.get("dire_score"), raw.get("radiant_score"))

        def label(p: dict[str, Any]) -> str:
            return p.get("personaname") or "Anonymous"

        def is_known(p: dict[str, Any]) -> bool:
            return p.get("account_id") not in (None, ANONYMOUS)

        mates = [p for p in team if p is not me]
        scoreboard = [
            ScoreRow(
                name=label(p),
                team="Radiant" if self._radiant(p) else "Dire",
                character=hero(p.get("hero_id"))[0],
                character_icon=hero(p.get("hero_id"))[1],
                is_self=p is me,
                rank=medal(p.get("rank_tier"))[0],
                stats={
                    "K/D/A": f"{p.get('kills') or 0}/{p.get('deaths') or 0}/{p.get('assists') or 0}",
                    "LH/DN": f"{p.get('last_hits') or 0}/{p.get('denies') or 0}",
                    "GPM": p.get("gold_per_min") or 0,
                    "XPM": p.get("xp_per_min") or 0,
                    "Damage": p.get("hero_damage") or 0,
                },
                extra={
                    "items": [u for u in (item_icon(p.get(f"item_{i}")) for i in range(6)) if u],
                    "won": bool(raw.get("radiant_win")) == self._radiant(p),
                },
            )
            for p in sorted(players, key=lambda p: p.get("player_slot") or 0)
        ]
        return Match(
            game="dota2",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mode,
            duration_s=duration,
            result="remake" if remake else ("win" if won else "loss"),
            character=name,
            character_icon=icon,
            role=role,
            metrics={
                "kills": k,
                "deaths": d,
                "assists": a,
                "kda": round((k + a) / max(d, 1), 2),
                "last_hits_per_min": round((me.get("last_hits") or 0) / minutes, 2),
                "gpm": me.get("gold_per_min") or 0,
                "xpm": me.get("xp_per_min") or 0,
                "damage_per_min": None if dmg is None else round(dmg / minutes, 1),
                "damage_share": None if dmg is None else round(100 * dmg / team_damage, 1),
                "kill_participation": round(100 * (k + a) / team_kills, 1),
                "tower_damage": None if dmg is None else me.get("tower_damage") or 0,
            },
            rank_label=rank_label,
            rank_value=rank_value,
            score_line=f"{mine or 0}–{theirs or 0}",
            teammates=[label(p) for p in mates],
            team_keys=[str(p["account_id"]) for p in mates if is_known(p)],
            items=[u for u in (item_icon(me.get(f"item_{i}")) for i in range(6)) if u],
            scoreboard=scoreboard,
            link=f"https://www.opendota.com/matches/{raw['match_id']}",
        )

    # ── demo ─────────────────────────────────────────────────────────────────
    def demo_profile(self) -> Profile:
        from clutch.games.demo_data import DOTA_DEMO_PROFILE

        p = Profile(**DOTA_DEMO_PROFILE)
        latest = next(
            (
                x
                for m in reversed(self.demo_matches())
                if m["lobby_type"] in RANKED_LOBBIES
                for x in m["players"]
                if str(x["account_id"]) == p.key
            ),
            None,
        )
        tier = latest["rank_tier"] if latest else None
        label, value = medal(tier)
        p.ranks = [
            {
                "queue": "Ranked",
                "label": label,
                "tier": (label or "").split()[0].lower(),
                "lp": None,
                "value": value,
                "icon": f"{RANK_ICONS}/rank_icon_{(tier or 0) // 10}.png",
                "wins": None,
                "losses": None,
            }
        ]
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_data import dota_matches

        return dota_matches()
