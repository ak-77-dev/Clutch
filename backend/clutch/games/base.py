"""The contract every game adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clutch.models import GameMeta, Match, Profile

DEMO_QUERIES = {"demo", "demo#demo"}


def is_demo_query(query: str) -> bool:
    return query.strip().lower() in DEMO_QUERIES


class GameProvider(ABC):
    meta: GameMeta

    # ── live data ────────────────────────────────────────────────────────────
    @abstractmethod
    def configured(self) -> bool:
        """True when the API key(s) this game needs are set."""

    @abstractmethod
    def resolve(self, query: str) -> Profile:
        """Look a player up by what a user types (Riot ID, platform id, ...).

        Raises ``clutch.http.NotFound`` when the player doesn't exist.
        """

    @abstractmethod
    def refresh_profile(self, profile: Profile) -> Profile:
        """Re-fetch rank / level for an already-resolved player."""

    @abstractmethod
    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        """Newest-first match ids for the player (live API)."""

    @abstractmethod
    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        """Raw match JSON exactly as the upstream API returns it."""

    # ── parsing ──────────────────────────────────────────────────────────────
    @abstractmethod
    def match_id_of(self, raw: dict[str, Any]) -> str:
        """The upstream id of a raw match."""

    @abstractmethod
    def match_date(self, raw: dict[str, Any]) -> str:
        """ISO date of a raw match (used for storage ordering)."""

    @abstractmethod
    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        """Raw match -> ``Match`` from ``profile``'s point of view (None if absent)."""

    # ── demo mode ────────────────────────────────────────────────────────────
    @abstractmethod
    def demo_profile(self) -> Profile: ...

    @abstractmethod
    def demo_matches(self) -> list[dict[str, Any]]:
        """Synthetic raw matches in the upstream API's shape."""

    def info(self) -> dict[str, Any]:
        return {**self.meta.to_dict(), "configured": self.configured()}
