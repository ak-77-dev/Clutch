"""Game adapters. Add a game by implementing ``GameProvider`` and listing it here."""

from __future__ import annotations

from clutch.games.base import GameProvider


def default_providers() -> list[GameProvider]:
    from clutch.games.league import LeagueProvider
    from clutch.games.rocketleague import RocketLeagueProvider
    from clutch.games.valorant import ValorantProvider

    return [LeagueProvider(), ValorantProvider(), RocketLeagueProvider()]
