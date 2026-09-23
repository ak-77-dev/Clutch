"""Game-agnostic data model.

Every game adapter turns its API's raw match JSON into ``Match`` objects:
common fields (result, date, mode, character) plus a flat ``metrics`` dict of
numbers. ``GameMeta`` describes those metrics (labels, formatting, which ones
are "process" stats worth correlating with winning), so the analytics engine
and the UI never need game-specific code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Fmt = Literal["int", "float1", "float2", "pct", "time"]


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    fmt: Fmt = "float1"
    higher_is_better: bool = True
    short: str | None = None  # compact column header


@dataclass(frozen=True)
class GameMeta:
    id: str
    name: str
    character_label: str  # "Champion", "Agent", "Car"
    search_hint: str  # placeholder for the search box
    accent: str  # brand color used by the UI
    metrics: tuple[Metric, ...]
    kpis: tuple[str, ...]  # metric keys shown as headline tiles
    trend_metrics: tuple[str, ...]  # metric keys charted over time
    factor_metrics: tuple[str, ...]  # process stats tested against win rate
    card_metrics: tuple[str, ...]  # metric keys shown on a match card

    def metric(self, key: str) -> Metric:
        for m in self.metrics:
            if m.key == key:
                return m
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["metrics"] = {m.key: asdict(m) for m in self.metrics}
        return d


@dataclass
class ScoreRow:
    """One player line in a match scoreboard."""

    name: str
    team: str
    character: str
    character_icon: str | None
    stats: dict[str, float | int | str | None]
    is_self: bool = False
    rank: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Match:
    game: str
    id: str
    date: str  # ISO-8601, UTC
    mode: str
    duration_s: float
    result: Literal["win", "loss", "draw", "remake"]
    character: str
    character_icon: str | None
    metrics: dict[str, float | None]  # None = the API didn't report it for this match
    role: str | None = None
    map: str | None = None
    rank_label: str | None = None
    rank_value: float | None = None  # monotonic ladder position, for charts
    score_line: str | None = None  # e.g. "13–9" or "3–1"
    teammates: list[str] = field(default_factory=list)
    team_keys: list[str] = field(default_factory=list)  # stable ids for duo stats
    items: list[str] = field(default_factory=list)  # icon URLs (LoL items)
    scoreboard: list[ScoreRow] = field(default_factory=list)
    link: str | None = None

    @property
    def won(self) -> bool:
        return self.result == "win"

    @property
    def counts(self) -> bool:
        """Remakes are excluded from every stat."""
        return self.result != "remake"

    def summary_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("scoreboard")
        return d

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    game: str
    key: str  # stable player id (puuid, platform:id, ...)
    name: str
    tag: str | None = None
    icon: str | None = None
    level: int | None = None
    region: str | None = None
    ranks: list[dict[str, Any]] = field(default_factory=list)  # [{queue, label, value, lp, wins, losses, icon}]
    demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
