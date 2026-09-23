"""Call of Duty via callofduty.com's (unofficial) stats API. EXPERIMENTAL.

Activision has no public API: its stats endpoints are only opened to partners
(Tracker Network etc.). They do answer requests signed in as the player
themselves, so this adapter uses the player's own ``ACT_SSO_COOKIE`` (copy it
from your browser after logging in at callofduty.com) as ``COD_SSO_TOKEN``.

- ``stats/cod/v1/title/{title}/platform/{p}/{lookup}/{id}/profile/type/mp``: level, prestige
- ``crm/cod/v2/title/{title}/platform/{p}/{lookup}/{id}/matches/mp/start/0/end/0/details``: last ~20 matches

The title code for the newest game is configurable (``COD_TITLE``) because
Activision picks a new one each year. Everything here was built from the
publicly documented shape of these endpoints and verified against the demo
data, not against live responses: expect to adjust it once you have a token.
Launching, playtime and clipping for CoD work without any of this.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

API = "https://www.callofduty.com/api/papi-client"
TITLE_NAMES = {
    "bo7": "Black Ops 7",
    "bo6": "Black Ops 6",
    "mw3": "Modern Warfare III",
    "mw2": "Modern Warfare II",
    "mw": "Modern Warfare (2019)",
    "cw": "Cold War",
    "vg": "Vanguard",
}
PLATFORMS = {"battle": "Battle.net", "uno": "Activision ID", "psn": "PlayStation", "xbl": "Xbox", "steam": "Steam"}
MODE_NAMES = {
    "war": "Team Deathmatch",
    "dom": "Domination",
    "hp": "Hardpoint",
    "koth": "Hardpoint",
    "sd": "Search and Destroy",
    "conf": "Kill Confirmed",
    "ctf": "Capture the Flag",
    "ffa": "Free-for-All",
    "gun": "Gun Game",
    "cntrl": "Control",
}

META = GameMeta(
    id="cod",
    name="Call of Duty",
    character_label="Mode",
    search_hint="Activision ID, e.g. Name#1234567 (needs COD_SSO_TOKEN)",
    accent="#f5a623",
    metrics=(
        Metric("kills", "Kills", "float1", short="K"),
        Metric("deaths", "Deaths", "float1", higher_is_better=False, short="D"),
        Metric("assists", "Assists", "float1", short="A"),
        Metric("kd", "K/D", "float2"),
        Metric("spm", "Score / min", "int", short="SPM"),
        Metric("damage_per_min", "Damage / min", "int", short="DPM"),
        Metric("damage_taken_per_min", "Damage taken / min", "int", higher_is_better=False, short="DTPM"),
        Metric("accuracy", "Accuracy", "pct", short="ACC"),
        Metric("headshot_pct", "Headshot kills", "pct", short="HS%"),
        Metric("longest_streak", "Longest streak", "int", short="STRK"),
    ),
    kpis=("kd", "spm", "damage_per_min", "accuracy", "headshot_pct"),
    trend_metrics=("kd", "spm", "damage_per_min"),
    factor_metrics=("accuracy", "damage_per_min", "headshot_pct", "deaths", "longest_streak"),
    card_metrics=("kd", "spm", "damage_per_min", "accuracy"),
)


def parse_player_id(query: str) -> tuple[str, str]:
    """``battle:Name#1234`` / ``psn:Name`` / ``Name#1234567`` -> (platform, id)."""
    q = query.strip()
    if ":" in q and q.split(":", 1)[0].lower() in PLATFORMS:
        platform, ident = q.split(":", 1)
        return platform.lower(), ident.strip()
    if re.search(r"#\d{6,}$", q):
        return "uno", q  # Activision IDs have a 7-digit suffix
    if re.search(r"#\d{3,5}$", q):
        return "battle", q  # BattleTags have 4-5 digits
    raise NotFound("Use an Activision ID (Name#1234567), a BattleTag (battle:Name#1234), psn:Name or xbl:Name")


def pretty_map(code: str | None) -> str | None:
    if not code:
        return None
    return re.sub(r"^mp_", "", code).replace("_", " ").title()


class CodProvider(GameProvider):
    meta = META

    def __init__(self, token: str | None = None, title: str | None = None, client: JsonClient | None = None) -> None:
        self.token = token if token is not None else os.environ.get("COD_SSO_TOKEN")
        self.title = (title or os.environ.get("COD_TITLE") or "bo7").lower()
        cookie = f"ACT_SSO_COOKIE={self.token}; atkn={self.token}; API_CSRF_TOKEN=clutch" if self.token else ""
        self.client = client or JsonClient(
            headers={"Cookie": cookie, "X-XSRF-TOKEN": "clutch", "Accept": "application/json"},
            limiter=SlidingWindowLimiter([(20, 60.0)]),  # unofficial: stay gentle
        )

    def configured(self) -> bool:
        return bool(self.token)

    def _get(self, path: str) -> Any:
        body = self.client.get(f"{API}/{path}")
        if isinstance(body, dict) and body.get("status") == "error":
            message = (body.get("data") or {}).get("message", "error")
            if "not permitted" in message.lower() or "not authenticated" in message.lower():
                raise NotFound("Activision refused the request: the SSO token is missing, expired, or this profile is private.")
            raise NotFound(f"Call of Duty API: {message}")
        return body.get("data") if isinstance(body, dict) else body

    def _path(self, platform: str, ident: str) -> str:
        lookup = "uno" if platform == "uno" else "gamer"
        return f"title/{self.title}/platform/{platform}/{lookup}/{quote(ident, safe='')}"

    # ── live API ─────────────────────────────────────────────────────────────
    def resolve(self, query: str) -> Profile:
        platform, ident = parse_player_id(query)
        name, _, tag = ident.partition("#")
        profile = Profile(game="cod", key=f"{platform}:{ident}", name=name, tag=tag or None, region=PLATFORMS.get(platform))
        return self.refresh_profile(profile)

    def refresh_profile(self, profile: Profile) -> Profile:
        platform, ident = profile.key.split(":", 1)
        data = self._get(f"stats/cod/v1/{self._path(platform, ident)}/profile/type/mp") or {}
        level, prestige = data.get("level"), data.get("prestige")
        profile.level = int(level) if level is not None else None
        profile.ranks = [
            {
                "queue": TITLE_NAMES.get(self.title, self.title.upper()),
                "label": f"Prestige {prestige}" if prestige else (f"Level {level}" if level else "Unranked"),
                "tier": "prestige" if prestige else "level",
                "lp": None,
                "value": float(prestige or 0) * 100 + float(level or 0),
                "wins": None,
                "losses": None,
            }
        ]
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        platform, ident = profile.key.split(":", 1)
        data = self._get(f"crm/cod/v2/{self._path(platform, ident)}/matches/mp/start/0/end/0/details") or {}
        matches = data.get("matches") or []
        self._page = {str(m["matchID"]): m for m in matches if m.get("matchID")}
        return list(self._page)[:limit]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        page = getattr(self, "_page", {})
        if match_id not in page:
            raise NotFound("Match details are only available from the recent-matches list")
        return page[match_id]

    # ── parsing ──────────────────────────────────────────────────────────────
    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["matchID"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return datetime.fromtimestamp(raw.get("utcStartSeconds") or 0, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        st = raw.get("playerStats") or {}
        if not st:
            return None
        seconds = float(st.get("timePlayed") or 0) or float(raw.get("utcEndSeconds", 0) - raw.get("utcStartSeconds", 0))
        minutes = max(seconds / 60, 0.5)
        k, d, a = int(st.get("kills") or 0), int(st.get("deaths") or 0), int(st.get("assists") or 0)
        shots = st.get("shotsFired") or 0
        result = (raw.get("result") or "").lower()
        mode = MODE_NAMES.get(raw.get("mode", ""), (raw.get("mode") or "Multiplayer").replace("_", " ").title())
        team_score = (raw.get("team1Score"), raw.get("team2Score"))
        mine_first = (raw.get("player") or {}).get("team") in ("allies", "team1", None)
        score_line = f"{team_score[0]}–{team_score[1]}" if mine_first else f"{team_score[1]}–{team_score[0]}"
        return Match(
            game="cod",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mode,
            duration_s=seconds,
            result="win" if result == "win" else "draw" if result == "draw" else "loss",
            character=mode,
            character_icon=None,
            map=pretty_map(raw.get("map")),
            metrics={
                "kills": k,
                "deaths": d,
                "assists": a,
                "kd": round(k / max(d, 1), 2),
                "spm": round(float(st.get("scorePerMinute") or (st.get("score") or 0) / minutes), 1),
                "damage_per_min": round((st.get("damageDone") or 0) / minutes, 1),
                "damage_taken_per_min": round((st.get("damageTaken") or 0) / minutes, 1),
                "accuracy": round(100 * (st.get("shotsLanded") or 0) / shots, 1)
                if shots
                else (st.get("accuracy") and round(100 * st["accuracy"], 1)),
                "headshot_pct": round(100 * (st.get("headshots") or 0) / k, 1) if k else 0.0,
                "longest_streak": int(st.get("longestStreak") or 0),
            },
            score_line=score_line if None not in team_score else None,
            scoreboard=[
                ScoreRow(
                    name=profile.name,
                    team=((raw.get("player") or {}).get("team") or "You").title(),
                    character=mode,
                    character_icon=None,
                    is_self=True,
                    stats={"K/D/A": f"{k}/{d}/{a}", "Score": st.get("score") or 0, "Damage": st.get("damageDone") or 0},
                )
            ],
        )

    # ── demo ─────────────────────────────────────────────────────────────────
    def demo_profile(self) -> Profile:
        from clutch.games.demo_data import COD_DEMO_PROFILE

        return Profile(**COD_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_data import cod_matches

        return cod_matches()
