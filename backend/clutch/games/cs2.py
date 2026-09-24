"""Counter-Strike 2 via the FACEIT Data API (https://docs.faceit.com/docs/data-api). Free key.

Valve publishes no CS2 match history, so this covers FACEIT matches, which is
where most people who care about their stats play.

- ``/players?nickname=`` (or ``?game=cs2&game_player_id=<steamid64>``) -> player, level, Elo
- ``/players/{id}/history?game=cs2`` -> match list
- ``/matches/{id}/stats`` -> per-player scoreboard (all values are strings)

The "character" is the map, so the pool view shows your best and worst maps.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

API = "https://open.faceit.com/data/v4"

META = GameMeta(
    id="cs2",
    name="Counter-Strike 2",
    character_label="Map",
    search_hint="FACEIT nickname or Steam ID",
    accent="#de9b35",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kd", "K/D", "float2"),
        Metric("kr", "Kills / round", "float2", short="K/R"),
        Metric("adr", "ADR", "float1"),
        Metric("hs_pct", "Headshot %", "pct", short="HS%"),
        Metric("mvps", "MVPs", "float1", short="MVP"),
        Metric("multikills", "Multi-kills (3K+)", "float1", short="3K+"),
    ),
    kpis=("kd", "adr", "hs_pct", "kr"),
    trend_metrics=("adr", "kd", "hs_pct"),
    factor_metrics=("adr", "hs_pct", "deaths", "mvps", "multikills", "kr"),
    card_metrics=("kd", "adr", "hs_pct", "mvps"),
)


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def map_name(raw: str | None) -> str:
    """'de_mirage' -> 'Mirage'."""
    if not raw:
        return "Unknown map"
    return re.sub(r"^(de|cs|ar)_", "", raw).replace("_", " ").title()


class Cs2Provider(GameProvider):
    meta = META

    def __init__(self, api_key: str | None = None, client: JsonClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("FACEIT_API_KEY")
        # FACEIT's default server key allows 10,000 requests an hour.
        self.client = client or JsonClient(
            headers={"Authorization": f"Bearer {self.api_key or ''}"}, limiter=SlidingWindowLimiter([(8, 1.0)])
        )
        self._history: dict[str, dict[str, Any]] = {}

    def configured(self) -> bool:
        return bool(self.api_key)

    def resolve(self, query: str) -> Profile:
        q = query.strip()
        params = {"game": "cs2", "game_player_id": q} if re.fullmatch(r"7656\d{13}", q) else {"nickname": q}
        try:
            player = self.client.get(f"{API}/players", params)
        except NotFound:
            raise NotFound("No FACEIT player by that nickname.") from None
        if "cs2" not in (player.get("games") or {}):
            raise NotFound(f"{player.get('nickname', q)} hasn't played CS2 on FACEIT.")
        return self._profile(player)

    def _profile(self, player: dict[str, Any]) -> Profile:
        cs2 = (player.get("games") or {}).get("cs2") or {}
        level, elo = cs2.get("skill_level"), cs2.get("faceit_elo")
        return Profile(
            game="cs2",
            key=player["player_id"],
            name=player.get("nickname") or player["player_id"],
            icon=player.get("avatar") or None,
            region=(cs2.get("region") or "").upper() or None,
            level=level,
            ranks=[
                {
                    "queue": "FACEIT",
                    "label": f"Level {level} · {elo} Elo" if level else "Unranked",
                    "tier": f"level {level}" if level else "unranked",
                    "lp": elo,
                    "value": float(elo) if elo else None,
                    "icon": f"https://cdn-frontend.faceit-cdn.net/web/static/media/assets_images_skill-icons_skill_level_{level}_svg.svg"
                    if level
                    else None,
                    "wins": None,
                    "losses": None,
                }
            ],
        )

    def refresh_profile(self, profile: Profile) -> Profile:
        return self._profile(self.client.get(f"{API}/players/{profile.key}"))

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        items = (self.client.get(f"{API}/players/{profile.key}/history", {"game": "cs2", "limit": min(limit, 100)}) or {}).get(
            "items"
        ) or []
        for it in items:
            self._history[it["match_id"]] = it
        return [it["match_id"] for it in items if it.get("status", "finished").lower() == "finished"]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        item = self._history.get(match_id) or self.client.get(f"{API}/matches/{match_id}")
        try:
            rounds = (self.client.get(f"{API}/matches/{match_id}/stats") or {}).get("rounds") or []
        except NotFound:  # cancelled / abandoned matches have no stats
            rounds = []
        keep = ("match_id", "started_at", "finished_at", "game_mode", "competition_name", "results")
        return {**{k: item.get(k) for k in keep}, "rounds": rounds}

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return raw["match_id"]

    def match_date(self, raw: dict[str, Any]) -> str:
        return datetime.fromtimestamp(raw.get("started_at") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        if not raw.get("rounds"):
            return None
        rnd = raw["rounds"][0]  # one map per match in CS2 queues
        teams = rnd.get("teams") or []
        mine = next((t for t in teams for p in t.get("players") or [] if p.get("player_id") == profile.key), None)
        if mine is None:
            return None
        me = next(p for p in mine["players"] if p.get("player_id") == profile.key)
        others = [t for t in teams if t is not mine]
        st = me.get("player_stats") or {}
        rs = rnd.get("round_stats") or {}
        k, d, a = _num(st.get("Kills")), _num(st.get("Deaths")), _num(st.get("Assists"))
        rounds_played = _num(rs.get("Rounds")) or (sum(_num(t.get("team_stats", {}).get("Final Score")) for t in teams) or 1)
        my_score = int(_num((mine.get("team_stats") or {}).get("Final Score")))
        their_score = int(_num(((others[0] if others else {}).get("team_stats") or {}).get("Final Score")))
        won = str((mine.get("team_stats") or {}).get("Team Win")) == "1"
        started, finished = raw.get("started_at") or 0, raw.get("finished_at") or 0
        mp = map_name(rs.get("Map"))
        return Match(
            game="cs2",
            id=raw["match_id"],
            date=self.match_date(raw),
            mode=raw.get("competition_name") or (raw.get("game_mode") or "5v5"),
            duration_s=float(max(0, finished - started)),
            result="draw" if my_score == their_score else ("win" if won else "loss"),
            character=mp,
            character_icon=None,
            map=mp,
            metrics={
                "kills": k,
                "deaths": d,
                "assists": a,
                "kd": round(k / max(d, 1), 2),
                "kr": round(k / rounds_played, 2),
                "adr": _num(st.get("ADR")) or None,
                "hs_pct": _num(st.get("Headshots %")),
                "mvps": _num(st.get("MVPs")),
                "multikills": _num(st.get("Triple Kills")) + _num(st.get("Quadro Kills")) + _num(st.get("Penta Kills")),
            },
            score_line=f"{my_score}–{their_score}",
            teammates=[p.get("nickname") or "?" for p in mine["players"] if p is not me],
            team_keys=[p["player_id"] for p in mine["players"] if p is not me and p.get("player_id")],
            scoreboard=[
                ScoreRow(
                    name=p.get("nickname") or "?",
                    team=(t.get("team_stats") or {}).get("Team") or t.get("team_id", "?"),
                    character=mp,
                    character_icon=None,
                    is_self=p.get("player_id") == profile.key,
                    stats={
                        "K/D/A": f"{int(_num(p['player_stats'].get('Kills')))}/{int(_num(p['player_stats'].get('Deaths')))}/{int(_num(p['player_stats'].get('Assists')))}",
                        "ADR": _num(p["player_stats"].get("ADR")),
                        "HS%": _num(p["player_stats"].get("Headshots %")),
                        "MVPs": int(_num(p["player_stats"].get("MVPs"))),
                    },
                    extra={"won": str((t.get("team_stats") or {}).get("Team Win")) == "1"},
                )
                for t in teams
                for p in sorted(t.get("players") or [], key=lambda p: -_num((p.get("player_stats") or {}).get("Kills")))
            ],
            link=f"https://www.faceit.com/en/cs2/room/{raw['match_id']}",
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import CS2_DEMO_PROFILE

        return Profile(**CS2_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import cs2_matches

        return cs2_matches()
