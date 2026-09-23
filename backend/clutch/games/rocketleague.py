"""Rocket League via ballchasing.com, reusing the ``rlstats`` package.

Players are identified by platform id (``steam:7656…``, ``epic:<id>``), which
is what ballchasing filters on; raw replays are parsed by ``rlstats.parse``.
"""

from __future__ import annotations

import os
from itertools import islice
from typing import Any

from rlstats.client import BallchasingClient
from rlstats.demo import DEMO_PLAYER, generate_replays
from rlstats.parse import PlayerRef, parse_replay
from rlstats.ranks import rank_label

from clutch.games.base import GameProvider
from clutch.http import NotFound
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

META = GameMeta(
    id="rocketleague",
    name="Rocket League",
    character_label="Car",
    search_hint="Platform ID, e.g. steam:76561198…",
    accent="#1f8bff",
    metrics=(
        Metric("score", "Score", "int"),
        Metric("goals", "Goals", "float2", short="G"),
        Metric("assists", "Assists", "float2", short="A"),
        Metric("saves", "Saves", "float2", short="Sv"),
        Metric("shots", "Shots", "float2", short="Sh"),
        Metric("shooting_pct", "Shooting %", "pct", short="Sh%"),
        Metric("demos", "Demos", "float2"),
        Metric("bpm", "Boost / min", "int", short="BPM"),
        Metric("pct_zero_boost", "Time at 0 boost", "pct", higher_is_better=False, short="0 boost"),
        Metric("pct_behind_ball", "Time behind ball", "pct", short="Behind"),
        Metric("pct_supersonic", "Time supersonic", "pct", short="SS"),
        Metric("avg_speed", "Avg speed", "int"),
    ),
    kpis=("goals", "assists", "saves", "shooting_pct", "score"),
    trend_metrics=("score", "goals", "saves"),
    factor_metrics=("pct_zero_boost", "pct_behind_ball", "pct_supersonic", "bpm", "avg_speed", "demos"),
    card_metrics=("goals", "assists", "saves", "score"),
)


class RocketLeagueProvider(GameProvider):
    meta = META

    def __init__(self, api_key: str | None = None, client: BallchasingClient | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("BALLCHASING_API_KEY")
        self._client = client

    @property
    def client(self) -> BallchasingClient:
        if self._client is None:
            self._client = BallchasingClient(self.api_key or "")
        return self._client

    def configured(self) -> bool:
        return bool(self.api_key)

    def resolve(self, query: str) -> Profile:
        ref = PlayerRef.parse(query.strip())
        if not ref.player_id:
            raise NotFound("Use a platform ID like steam:76561198… or epic:<id>")
        key = f"{ref.platform}:{ref.player_id}"
        summary = next(iter(self.client.iter_replays(**{"player-id": key, "count": 1})), None)
        if summary is None:
            raise NotFound("No public ballchasing replays for that player")
        name = key
        for team in ("blue", "orange"):
            for p in (summary.get(team) or {}).get("players", []):
                pid = p.get("id") or {}
                if f"{pid.get('platform')}:{pid.get('id')}" == key:
                    name = p.get("name") or key
        return Profile(game="rocketleague", key=key, name=name, region=ref.platform)

    def refresh_profile(self, profile: Profile) -> Profile:
        return profile  # ranks are derived from the latest replays (see overview)

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        return [str(r["id"]) for r in islice(self.client.iter_replays(**{"player-id": profile.key}), limit)]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        return self.client.replay(match_id)

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["id"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return str(raw.get("date") or raw.get("created") or "")

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        rm = parse_replay(raw, PlayerRef.parse(profile.key, profile.name))
        if rm is None:
            return None
        scoreboard = []
        for color in ("blue", "orange"):
            for p in (raw.get(color) or {}).get("players", []) or []:
                core = (p.get("stats") or {}).get("core") or {}
                pid = p.get("id") or {}
                scoreboard.append(
                    ScoreRow(
                        name=p.get("name", "?"),
                        team=color.title(),
                        character=p.get("car_name") or "Car",
                        character_icon=None,
                        is_self=f"{pid.get('platform')}:{pid.get('id')}" == profile.key,
                        rank=(p.get("rank") or {}).get("name"),
                        stats={
                            "Score": core.get("score", 0),
                            "G": core.get("goals", 0),
                            "A": core.get("assists", 0),
                            "Sv": core.get("saves", 0),
                            "Sh": core.get("shots", 0),
                        },
                        extra={"mvp": bool(p.get("mvp") or core.get("mvp")), "won": (color == rm.team) == rm.won},
                    )
                )
        return Match(
            game="rocketleague",
            id=rm.id,
            date=rm.date,
            mode=rm.playlist_name,
            duration_s=rm.duration,
            result="win" if rm.won else "loss",
            character=rm.car or "Car",
            character_icon=None,
            map=rm.map_name or None,
            metrics={
                "score": rm.score,
                "goals": rm.goals,
                "assists": rm.assists,
                "saves": rm.saves,
                "shots": rm.shots,
                "shooting_pct": rm.shooting_pct,
                "demos": rm.demos_inflicted,
                **{
                    k: getattr(rm, k)
                    for k in ("bpm", "pct_zero_boost", "pct_behind_ball", "pct_supersonic", "avg_speed")
                    if getattr(rm, k) is not None
                },
            },
            rank_label=rm.rank_name or (rank_label(rm.rank_value) if rm.rank_value else None),
            rank_value=rm.rank_value,
            score_line=f"{rm.team_goals}–{rm.opponent_goals}" + (" OT" if rm.overtime else ""),
            teammates=[t.name for t in rm.teammates],
            team_keys=[t.key for t in rm.teammates],
            scoreboard=scoreboard,
            link=rm.link,
        )

    def demo_profile(self) -> Profile:
        return Profile(
            game="rocketleague",
            key=f"{DEMO_PLAYER['platform']}:{DEMO_PLAYER['id']}",
            name=DEMO_PLAYER["name"],
            region="steam",
            demo=True,
        )

    def demo_matches(self) -> list[dict[str, Any]]:
        return generate_replays(games=220, seed=21)
