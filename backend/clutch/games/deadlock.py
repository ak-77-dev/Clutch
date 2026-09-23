"""Deadlock via deadlock-api.com (https://api.deadlock-api.com/docs). No API key needed.

- ``/v1/players/steam-search`` and ``/v1/players/steam``: find a player, name, avatar
- ``/v1/players/{id}/rank``: current badge (tier × 10 + subrank)
- ``/v1/players/{id}/match-history`` + ``/v1/matches/{id}/metadata``: matches and scoreboards
- Hero art and rank names from a bundled snapshot of ``/v1/assets`` (``scripts/snapshot_assets.py``)

Match metadata is ~1.7 MB per game (per-death details, damage matrices, item
timelines), so ``fetch_match`` keeps only the end-of-game scoreboard. Matches
the API hasn't cached yet come from Steam at a few requests per hour; those
fail with 429 and are picked up by the next sync.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from clutch.games._deadlock_assets import HEROES, RANKS
from clutch.games.base import GameProvider
from clutch.games.dota import account_id_from
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

API = "https://api.deadlock-api.com/v1"
TEAMS = {0: "Amber", 1: "Sapphire"}
MATCH_MODES = {1: "Unranked", 2: "Private Lobby", 3: "Bot Match", 4: "Ranked", 8: "Calibration"}
STREET_BRAWL = 4  # game_mode for the 4v4 round-based mode

MATCH_FIELDS = ("match_id", "start_time", "duration_s", "winning_team", "match_mode", "game_mode")
PLAYER_FIELDS = ("account_id", "player_slot", "team", "hero_id", "kills", "deaths", "assists", "net_worth", "last_hits", "denies", "level")
STAT_FIELDS = (
    "player_damage",
    "player_damage_taken",
    "player_healing",
    "boss_damage",
    "shots_hit",
    "shots_missed",
    "hero_bullets_hit",
    "hero_bullets_hit_crit",
)

META = GameMeta(
    id="deadlock",
    name="Deadlock",
    character_label="Hero",
    search_hint="Steam name or account ID",
    accent="#c8a15a",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kda", "KDA", "float2"),
        Metric("souls_per_min", "Souls / min", "int", short="SPM"),
        Metric("damage_per_min", "Hero damage / min", "int", short="DPM"),
        Metric("damage_taken_per_min", "Damage taken / min", "int", higher_is_better=False, short="DTPM"),
        Metric("healing_per_min", "Healing / min", "int", short="HPM"),
        Metric("accuracy", "Accuracy", "pct", short="ACC"),
        Metric("crit_pct", "Headshot %", "pct", short="HS%"),
        Metric("kill_participation", "Kill participation", "pct", short="KP"),
        Metric("objective_damage", "Objective damage", "int", short="OBJ"),
    ),
    kpis=("kda", "souls_per_min", "damage_per_min", "accuracy", "kill_participation"),
    trend_metrics=("souls_per_min", "kda", "damage_per_min"),
    factor_metrics=("souls_per_min", "deaths", "accuracy", "crit_pct", "kill_participation", "objective_damage"),
    card_metrics=("kda", "souls_per_min", "damage_per_min", "accuracy"),
)


def hero(hero_id: int | None) -> tuple[str, str | None, str | None]:
    h = HEROES.get(hero_id or 0)
    return (h[0], h[1], (h[2] or "").title() or None) if h else (f"Hero {hero_id}", None, None)


def badge(value: int | None) -> tuple[str | None, float | None, str | None]:
    """Badge 11..116 (tier × 10 + subrank 1-6) -> ("Mystic 5", ladder value, icon URL)."""
    if not value or value < 11:
        return None, None, None
    tier, sub = divmod(value, 10)
    if tier not in RANKS or not 1 <= sub <= 6:
        return None, None, None
    return f"{RANKS[tier]} {sub}", float((tier - 1) * 6 + sub - 1), f"{API}/assets/ranks/{tier}/{sub}/image"


def slim_match(raw: dict[str, Any]) -> dict[str, Any]:
    info = raw.get("match_info", raw)
    slim = {k: info.get(k) for k in MATCH_FIELDS}
    slim["players"] = []
    for p in info.get("players") or []:
        row = {k: p.get(k) for k in PLAYER_FIELDS}
        stats = p.get("stats") or {}
        # The API sends time-series snapshots (the last is end of game); already-slim rows hold a dict.
        final = stats[-1] if isinstance(stats, list) else stats
        row["stats"] = {k: final.get(k) for k in STAT_FIELDS}
        row["badge"] = (p.get("player_rank_data") or {}).get("initial_display_rank")
        slim["players"].append(row)
    return slim


class DeadlockProvider(GameProvider):
    meta = META

    def __init__(self, client: JsonClient | None = None) -> None:
        # Cached reads allow 100 requests / 10 s per IP.
        self.client = client or JsonClient(limiter=SlidingWindowLimiter([(100, 10.0)]))
        self._names: dict[int, str] = {}

    def configured(self) -> bool:
        return True  # works without a key

    # ── live API ─────────────────────────────────────────────────────────────
    def resolve(self, query: str) -> Profile:
        account_id = account_id_from(query)
        if account_id is None:
            hits = self.client.get(f"{API}/players/steam-search", {"search_query": query.strip(), "limit": 10}) or []
            exact = [h for h in hits if (h.get("personaname") or "").lower() == query.strip().lower()]
            if not (exact or hits):
                raise NotFound("No Deadlock player by that name. Try their Steam account ID.")
            account_id = (exact or hits)[0]["account_id"]  # results are ranked by recent activity
        return self.refresh_profile(Profile(game="deadlock", key=str(account_id), name=str(account_id)))

    def refresh_profile(self, profile: Profile) -> Profile:
        steam = self.client.get(f"{API}/players/steam", {"account_ids": profile.key}) or []
        if not steam:
            raise NotFound("No Steam profile for that account.")
        profile.name = steam[0].get("personaname") or profile.name
        profile.icon = steam[0].get("avatarmedium") or steam[0].get("avatar")
        try:
            rank = self.client.get(f"{API}/players/{profile.key}/rank")
        except NotFound:
            rank = {}
        label, value, icon = badge(rank.get("badge"))
        profile.ranks = [
            {
                "queue": "Ranked",
                "label": label or "Unranked",
                "tier": (label or "unranked").split()[0].lower(),
                "lp": None,
                "value": value,
                "icon": icon,
                "wins": None,
                "losses": None,
            }
        ]
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        rows = self.client.get(f"{API}/players/{profile.key}/match-history") or []
        rows.sort(key=lambda r: r.get("start_time") or 0, reverse=True)
        # Metadata is large even when cached; keep the first sync to a handful of games.
        return [str(r["match_id"]) for r in rows[: min(limit, 15)]]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        slim = slim_match(self.client.get(f"{API}/matches/{match_id}/metadata"))
        # Metadata has account ids only; attach names so the stored match is self-contained.
        missing = [p["account_id"] for p in slim["players"] if p.get("account_id") and p["account_id"] not in self._names]
        if missing:
            try:
                for s in self.client.get(f"{API}/players/steam", {"account_ids": ",".join(map(str, missing))}) or []:
                    self._names[s["account_id"]] = s.get("personaname") or str(s["account_id"])
            except NotFound:
                pass
        for p in slim["players"]:
            p["name"] = self._names.get(p.get("account_id"))
        return slim

    # ── parsing ──────────────────────────────────────────────────────────────
    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["match_id"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return datetime.fromtimestamp(raw.get("start_time") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        players = raw.get("players") or []
        me = next((p for p in players if str(p.get("account_id")) == profile.key), None)
        if me is None:
            return None
        duration = float(raw.get("duration_s") or 0)
        minutes = max(duration / 60, 1)
        team = [p for p in players if p.get("team") == me.get("team")]
        my_kills = sum(p.get("kills") or 0 for p in team)
        team_kills = my_kills or 1
        opp_kills = sum(p.get("kills") or 0 for p in players if p.get("team") != me.get("team"))
        st = me.get("stats") or {}
        k, d, a = me.get("kills") or 0, me.get("deaths") or 0, me.get("assists") or 0
        shots = (st.get("shots_hit") or 0) + (st.get("shots_missed") or 0)
        hero_hits = st.get("hero_bullets_hit") or 0

        mode = "Street Brawl" if raw.get("game_mode") == STREET_BRAWL else MATCH_MODES.get(raw.get("match_mode"), "Custom")
        rank_label, rank_value, _ = badge(me.get("badge")) if mode == "Ranked" else (None, None, None)
        name, icon, role = hero(me.get("hero_id"))

        def label(p: dict[str, Any]) -> str:
            return p.get("name") or str(p.get("account_id") or "?")

        def per_min(v: Any) -> float | None:
            return None if v is None else round(v / minutes, 1)

        mates = [p for p in team if p is not me]
        scoreboard = [
            ScoreRow(
                name=label(p),
                team=TEAMS.get(p.get("team"), "?"),
                character=hero(p.get("hero_id"))[0],
                character_icon=hero(p.get("hero_id"))[1],
                is_self=p is me,
                rank=badge(p.get("badge"))[0],
                stats={
                    "K/D/A": f"{p.get('kills') or 0}/{p.get('deaths') or 0}/{p.get('assists') or 0}",
                    "Souls": p.get("net_worth") or 0,
                    "Damage": (p.get("stats") or {}).get("player_damage") or 0,
                    "Healing": (p.get("stats") or {}).get("player_healing") or 0,
                    "LH": p.get("last_hits") or 0,
                },
                extra={"won": p.get("team") == raw.get("winning_team")},
            )
            for p in sorted(players, key=lambda p: (p.get("team") or 0, -(p.get("net_worth") or 0)))
        ]
        return Match(
            game="deadlock",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mode,
            duration_s=duration,
            # Games that end in the first few minutes are abandons, like LoL remakes.
            result="remake" if duration < 300 else ("win" if me.get("team") == raw.get("winning_team") else "loss"),
            character=name,
            character_icon=icon,
            role=role,
            metrics={
                "kills": k,
                "deaths": d,
                "assists": a,
                "kda": round((k + a) / max(d, 1), 2),
                "souls_per_min": per_min(me.get("net_worth")),
                "damage_per_min": per_min(st.get("player_damage")),
                "damage_taken_per_min": per_min(st.get("player_damage_taken")),
                "healing_per_min": per_min(st.get("player_healing")),
                "accuracy": round(100 * (st.get("shots_hit") or 0) / shots, 1) if shots else None,
                "crit_pct": round(100 * (st.get("hero_bullets_hit_crit") or 0) / hero_hits, 1) if hero_hits else None,
                "kill_participation": round(100 * (k + a) / team_kills, 1),
                "objective_damage": st.get("boss_damage"),
            },
            rank_label=rank_label,
            rank_value=rank_value,
            score_line=f"{my_kills}–{opp_kills}",
            teammates=[label(p) for p in mates],
            team_keys=[str(p["account_id"]) for p in mates if p.get("account_id")],
            scoreboard=scoreboard,
            link=f"https://statlocker.gg/match/{raw['match_id']}",
        )

    # ── demo ─────────────────────────────────────────────────────────────────
    def demo_profile(self) -> Profile:
        from clutch.games.demo_data import DEADLOCK_DEMO_PROFILE

        p = Profile(**DEADLOCK_DEMO_PROFILE)
        latest = next(
            (x for m in reversed(self.demo_matches()) if m["match_mode"] == 4 for x in m["players"] if str(x["account_id"]) == p.key),
            None,
        )
        label, value, icon = badge(latest["badge"] if latest else None)
        p.ranks = [
            {
                "queue": "Ranked",
                "label": label,
                "tier": (label or "").split()[0].lower(),
                "lp": None,
                "value": value,
                "icon": icon,
                "wins": None,
                "losses": None,
            }
        ]
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_data import deadlock_matches

        return deadlock_matches()
