"""Turn a raw ballchasing.com replay (``GET /api/replays/{id}``) into a ``Match``.

The raw JSON is stored untouched in SQLite and parsed on read, so improving
this module re-derives every historical match without re-downloading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from rlstats.ranks import rank_value


@dataclass(frozen=True)
class PlayerRef:
    """How the tracked player is identified: platform id first, name as fallback."""

    platform: str | None = None
    player_id: str | None = None
    name: str | None = None

    @classmethod
    def parse(cls, player_id: str | None = None, name: str | None = None) -> PlayerRef:
        """``player_id`` is ``platform:id`` (e.g. ``steam:7656119...``, ``epic:abc``)."""
        platform = pid = None
        if player_id and ":" in player_id:
            platform, pid = player_id.split(":", 1)
            platform, pid = platform.strip().lower(), pid.strip()
        return cls(platform or None, pid or None, (name or "").strip() or None)

    def matches(self, player: dict[str, Any]) -> bool:
        pid = player.get("id") or {}
        if self.player_id:
            return str(pid.get("id")) == self.player_id and (
                not self.platform or str(pid.get("platform", "")).lower() == self.platform
            )
        return bool(self.name) and str(player.get("name", "")).strip().lower() == self.name.lower()

    def __bool__(self) -> bool:
        return bool(self.player_id or self.name)


@dataclass(frozen=True)
class Teammate:
    key: str
    name: str


@dataclass
class Match:
    """One replay from the tracked player's point of view."""

    id: str
    date: str
    playlist_id: str
    playlist_name: str
    team_size: int
    duration: float
    overtime: bool
    map_name: str
    team: str
    won: bool
    team_goals: int
    opponent_goals: int
    # core
    score: float
    goals: float
    assists: float
    saves: float
    shots: float
    shooting_pct: float
    mvp: bool
    demos_inflicted: float
    demos_taken: float
    # boost
    bpm: float | None
    avg_boost: float | None
    boost_stolen: float | None
    pct_zero_boost: float | None
    pct_full_boost: float | None
    # movement
    avg_speed: float | None
    pct_supersonic: float | None
    pct_ground: float | None
    pct_high_air: float | None
    # positioning
    pct_defensive_third: float | None
    pct_offensive_third: float | None
    pct_behind_ball: float | None
    avg_distance_to_ball: float | None
    # context
    rank_tier: int | None
    rank_division: int | None
    rank_name: str | None
    car: str | None
    teammates: list[Teammate] = field(default_factory=list)
    opponents: list[str] = field(default_factory=list)
    lobby: dict[str, float] = field(default_factory=dict)
    """Average of each LOBBY_STATS stat over every other player in the match."""

    @property
    def rank_value(self) -> float | None:
        return rank_value(self.rank_tier, self.rank_division)

    @property
    def link(self) -> str:
        return f"https://ballchasing.com/replay/{self.id}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["teammates"] = [asdict(t) for t in self.teammates]
        d["rank_value"] = self.rank_value
        d["link"] = self.link
        return d


# Per-player stats compared against the lobby for the playstyle profile.
LOBBY_STATS = (
    "score",
    "goals",
    "assists",
    "saves",
    "shots",
    "demos_inflicted",
    "bpm",
    "avg_speed",
    "pct_supersonic",
    "pct_high_air",
    "pct_defensive_third",
    "pct_offensive_third",
    "pct_behind_ball",
    "pct_zero_boost",
)


class ParseError(ValueError):
    pass


def _num(v: Any, default: float | None = 0.0) -> float | None:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _player_stats(p: dict[str, Any]) -> dict[str, float | None]:
    s = p.get("stats") or {}
    core, boost = s.get("core") or {}, s.get("boost") or {}
    move, pos, demo = s.get("movement") or {}, s.get("positioning") or {}, s.get("demo") or {}
    shots, goals = _num(core.get("shots")), _num(core.get("goals"))
    shooting = core.get("shooting_percentage")
    return {
        "score": _num(core.get("score")),
        "goals": goals,
        "assists": _num(core.get("assists")),
        "saves": _num(core.get("saves")),
        "shots": shots,
        "shooting_pct": _num(shooting) if shooting is not None else (100 * goals / shots if shots else 0.0),
        "demos_inflicted": _num(demo.get("inflicted")),
        "demos_taken": _num(demo.get("taken")),
        "bpm": _num(boost.get("bpm"), None),
        "avg_boost": _num(boost.get("avg_amount"), None),
        "boost_stolen": _num(boost.get("amount_stolen"), None),
        "pct_zero_boost": _num(boost.get("percent_zero_boost"), None),
        "pct_full_boost": _num(boost.get("percent_full_boost"), None),
        "avg_speed": _num(move.get("avg_speed"), None),
        "pct_supersonic": _num(move.get("percent_supersonic_speed"), None),
        "pct_ground": _num(move.get("percent_ground"), None),
        "pct_high_air": _num(move.get("percent_high_air"), None),
        "pct_defensive_third": _num(pos.get("percent_defensive_third"), None),
        "pct_offensive_third": _num(pos.get("percent_offensive_third"), None),
        "pct_behind_ball": _num(pos.get("percent_behind_ball"), None),
        "avg_distance_to_ball": _num(pos.get("avg_distance_to_ball"), None),
    }


def _player_key(p: dict[str, Any]) -> str:
    pid = p.get("id") or {}
    if pid.get("id"):
        return f"{pid.get('platform', 'unknown')}:{pid['id']}"
    return f"name:{str(p.get('name', '')).lower()}"


def _team_goals(team: dict[str, Any]) -> int:
    goals = ((team.get("stats") or {}).get("core") or {}).get("goals")
    if goals is None:  # older payloads: sum player goals
        goals = sum(_num(((p.get("stats") or {}).get("core") or {}).get("goals")) or 0 for p in team.get("players", []))
    return int(goals or 0)


def find_player(replay: dict[str, Any], who: PlayerRef) -> tuple[str, dict[str, Any]] | None:
    for color in ("blue", "orange"):
        for p in (replay.get(color) or {}).get("players", []) or []:
            if who.matches(p):
                return color, p
    return None


def parse_replay(replay: dict[str, Any], who: PlayerRef) -> Match | None:
    """Parse a replay; ``None`` when the tracked player isn't in it."""
    if replay.get("status") not in (None, "ok"):
        raise ParseError(f"replay {replay.get('id')} not processed yet (status={replay.get('status')})")
    found = find_player(replay, who)
    if not found:
        return None
    color, me = found
    other = "orange" if color == "blue" else "blue"
    my_team, opp_team = replay.get(color) or {}, replay.get(other) or {}
    team_goals, opp_goals = _team_goals(my_team), _team_goals(opp_team)
    stats = _player_stats(me)
    my_key = _player_key(me)

    others = [p for c in ("blue", "orange") for p in (replay.get(c) or {}).get("players", []) or [] if _player_key(p) != my_key]
    lobby: dict[str, float] = {}
    for key in LOBBY_STATS:
        vals = [v for v in (_player_stats(p)[key] for p in others) if v is not None]
        if vals:
            lobby[key] = sum(vals) / len(vals)

    rank = me.get("rank") or {}
    core = (me.get("stats") or {}).get("core") or {}
    team_size = int(replay.get("team_size") or max(len(my_team.get("players", [])), 1))
    return Match(
        id=str(replay["id"]),
        date=str(replay.get("date") or replay.get("created") or ""),
        playlist_id=str(replay.get("playlist_id") or "unknown"),
        playlist_name=str(replay.get("playlist_name") or replay.get("playlist_id") or "Unknown"),
        team_size=team_size,
        duration=_num(replay.get("duration")) or 0.0,
        overtime=bool(replay.get("overtime")),
        map_name=str(replay.get("map_name") or replay.get("map_code") or ""),
        team=color,
        won=team_goals > opp_goals,
        team_goals=team_goals,
        opponent_goals=opp_goals,
        mvp=bool(me.get("mvp") or core.get("mvp")),
        rank_tier=rank.get("tier"),
        rank_division=rank.get("division"),
        rank_name=rank.get("name"),
        car=me.get("car_name"),
        teammates=[
            Teammate(_player_key(p), str(p.get("name", "?")))
            for p in my_team.get("players", []) or []
            if _player_key(p) != my_key
        ],
        opponents=[str(p.get("name", "?")) for p in opp_team.get("players", []) or []],
        lobby=lobby,
        **stats,  # type: ignore[arg-type]
    )
