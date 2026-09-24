"""osu! (standard) via the osu! API v2 (https://osu.ppy.sh/docs). Free OAuth client.

Create an OAuth application at https://osu.ppy.sh/home/account/edit (any callback
URL works) and set OSU_CLIENT_ID and OSU_CLIENT_SECRET. Clutch uses the
client-credentials flow, so it only reads public data.

A "match" is one play. The API only returns the last 24 hours of recent plays,
so history builds up as Clutch syncs; the first sync also pulls your top 100.
Passing the map counts as a win. The "character" is the beatmap set.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests

from clutch.games.base import GameProvider
from clutch.http import AuthFailed, JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

API = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_VERSION = "20240529"  # score objects in the current ("lazer") shape

META = GameMeta(
    id="osu",
    name="osu!",
    character_label="Beatmap",
    search_hint="osu! username",
    accent="#ff66aa",
    metrics=(
        Metric("accuracy", "Accuracy", "pct", short="ACC"),
        Metric("pp", "Performance", "int", short="PP"),
        Metric("stars", "Star rating", "float2", short="★"),
        Metric("misses", "Misses", "float1", higher_is_better=False, short="MISS"),
        Metric("max_combo", "Max combo", "int", short="COMBO"),
        Metric("length_min", "Map length (min)", "float1", short="LEN"),
    ),
    kpis=("accuracy", "pp", "misses", "stars"),
    trend_metrics=("accuracy", "pp", "stars"),
    factor_metrics=("stars", "length_min", "misses"),
    card_metrics=("accuracy", "pp", "stars", "misses"),
)


def mods_label(mods: list[Any] | None) -> str:
    names = [m.get("acronym") if isinstance(m, dict) else str(m) for m in mods or []]
    names = [n for n in names if n and n != "CL"]  # "classic" is implied on stable scores
    return "+".join(names) if names else "No mod"


class OsuProvider(GameProvider):
    meta = META

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, client: JsonClient | None = None) -> None:
        self.client_id = client_id if client_id is not None else os.environ.get("OSU_CLIENT_ID")
        self.client_secret = client_secret if client_secret is not None else os.environ.get("OSU_CLIENT_SECRET")
        self.client = client or JsonClient(headers={"x-api-version": API_VERSION}, limiter=SlidingWindowLimiter([(60, 60.0)]))
        self._token_until = 0.0
        self._lock = threading.Lock()
        self._scores: dict[str, dict[str, Any]] = {}

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _auth(self) -> None:
        with self._lock:
            if time.time() < self._token_until:
                return
            resp = requests.post(
                TOKEN_URL,
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                    "scope": "public",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                raise AuthFailed("osu! rejected the client id / secret", resp.status_code)
            body = resp.json()
            self.client.session.headers["Authorization"] = f"Bearer {body['access_token']}"
            self._token_until = time.time() + body.get("expires_in", 3600) - 60

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._auth()
        return self.client.get(f"{API}{path}", params)

    def resolve(self, query: str) -> Profile:
        name = query.strip()
        try:
            user = self._get(f"/users/@{name}/osu") if not name.isdigit() else self._get(f"/users/{name}/osu")
        except NotFound:
            raise NotFound("No osu! player by that name.") from None
        return self._profile(user)

    def _profile(self, u: dict[str, Any]) -> Profile:
        st = u.get("statistics") or {}
        rank, pp = st.get("global_rank"), st.get("pp")
        return Profile(
            game="osu",
            key=str(u["id"]),
            name=u.get("username") or str(u["id"]),
            icon=u.get("avatar_url"),
            region=u.get("country_code"),
            level=(st.get("level") or {}).get("current"),
            ranks=[
                {
                    "queue": "osu! standard",
                    "label": f"#{rank:,} · {pp:,.0f}pp" if rank and pp is not None else "Unranked",
                    "tier": "global",
                    "lp": st.get("country_rank"),
                    "value": float(pp) if pp else None,
                    "wins": None,
                    "losses": None,
                }
            ],
        )

    def refresh_profile(self, profile: Profile) -> Profile:
        return self._profile(self._get(f"/users/{profile.key}/osu"))

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        scores = self._get(f"/users/{profile.key}/scores/recent", {"include_fails": 1, "mode": "osu", "limit": min(limit, 100)}) or []
        if not self._scores:  # the recent list only covers 24 h; seed history with the player's best plays
            scores += self._get(f"/users/{profile.key}/scores/best", {"mode": "osu", "limit": min(limit, 100)}) or []
        for s in scores:
            self._scores[str(s["id"])] = s
        ordered = sorted(scores, key=lambda s: s.get("ended_at") or s.get("created_at") or "", reverse=True)
        return list(dict.fromkeys(str(s["id"]) for s in ordered))

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return self._scores.get(match_id) or self._get(f"/scores/{match_id}")

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["id"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return (raw.get("ended_at") or raw.get("created_at") or "1970-01-01T00:00:00Z").replace("+00:00", "Z")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        if str(raw.get("user_id") or (raw.get("user") or {}).get("id")) != profile.key:
            return None
        bm, bms = raw.get("beatmap") or {}, raw.get("beatmapset") or {}
        st = raw.get("statistics") or {}
        passed = bool(raw.get("passed")) and raw.get("rank") != "F"
        length = float(bm.get("total_length") or 0)
        acc = raw.get("accuracy")
        title = bms.get("title") or "Unknown map"
        return Match(
            game="osu",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mods_label(raw.get("mods")),
            duration_s=length,
            result="win" if passed else "loss",
            character=title,
            character_icon=(bms.get("covers") or {}).get("list"),
            role=bm.get("version"),
            metrics={
                "accuracy": round(acc * 100, 2) if acc is not None else None,
                "pp": round(raw["pp"]) if raw.get("pp") is not None else None,
                "stars": bm.get("difficulty_rating"),
                "misses": st.get("miss", st.get("count_miss", 0)) or 0,
                "max_combo": raw.get("max_combo"),
                "length_min": round(length / 60, 2) if length else None,
            },
            score_line=raw.get("rank") or "F",
            teammates=[],
            scoreboard=[
                ScoreRow(
                    name=(raw.get("user") or {}).get("username") or profile.name,
                    team=bm.get("version") or "",
                    character=f"{bms.get('artist', '')} – {title}".strip(" –"),
                    character_icon=(bms.get("covers") or {}).get("list"),
                    is_self=True,
                    stats={
                        "Rank": raw.get("rank") or "F",
                        "Acc": f"{(acc or 0) * 100:.2f}%",
                        "Combo": raw.get("max_combo") or 0,
                        "300/100/50/X": f"{st.get('great', 0)}/{st.get('ok', 0)}/{st.get('meh', 0)}/{st.get('miss', 0)}",
                    },
                    extra={"won": passed},
                )
            ],
            link=f"https://osu.ppy.sh/scores/{raw['id']}",
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import OSU_DEMO_PROFILE

        return Profile(**OSU_DEMO_PROFILE)

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import osu_matches

        return osu_matches()
