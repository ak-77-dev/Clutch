"""Game adapters. Add a game by implementing ``GameProvider`` and listing it here."""

from __future__ import annotations

from clutch.games.base import GameProvider


def default_providers() -> list[GameProvider]:
    from clutch.games.chess import ChessComProvider, LichessProvider
    from clutch.games.cod import CodProvider
    from clutch.games.cs2 import Cs2Provider
    from clutch.games.deadlock import DeadlockProvider
    from clutch.games.dota import DotaProvider
    from clutch.games.league import LeagueProvider
    from clutch.games.osu import OsuProvider
    from clutch.games.pubg import PubgProvider
    from clutch.games.rocketleague import RocketLeagueProvider
    from clutch.games.supercell import BrawlStarsProvider, ClashRoyaleProvider
    from clutch.games.tft import TftProvider
    from clutch.games.valorant import ValorantProvider

    return [
        LeagueProvider(),
        ValorantProvider(),
        Cs2Provider(),
        RocketLeagueProvider(),
        DotaProvider(),
        DeadlockProvider(),
        CodProvider(),
        TftProvider(),
        PubgProvider(),
        BrawlStarsProvider(),
        ClashRoyaleProvider(),
        OsuProvider(),
        ChessComProvider(),
        LichessProvider(),
    ]
