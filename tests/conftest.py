from __future__ import annotations

import copy
from typing import Any

import pytest

from rlstats.demo import DEMO_PLAYER, generate_replays
from rlstats.parse import PlayerRef, parse_replay


def make_player(name: str, pid: str, platform: str = "steam", **core: Any) -> dict[str, Any]:
    stats = {"score": 300, "goals": 1, "assists": 0, "saves": 1, "shots": 2, **core}
    return {
        "name": name,
        "id": {"platform": platform, "id": pid},
        "rank": {"tier": 13, "division": 2, "name": "Diamond I Div 2"},
        "car_name": "Octane",
        "mvp": False,
        "stats": {
            "core": stats,
            "boost": {"bpm": 400, "avg_amount": 45, "percent_zero_boost": 10, "percent_full_boost": 8},
            "movement": {"avg_speed": 1550, "percent_supersonic_speed": 12, "percent_ground": 58, "percent_high_air": 5},
            "positioning": {
                "percent_defensive_third": 45,
                "percent_offensive_third": 28,
                "percent_behind_ball": 65,
                "avg_distance_to_ball": 2600,
            },
            "demo": {"inflicted": 1, "taken": 0},
        },
    }


@pytest.fixture
def replay() -> dict[str, Any]:
    """A minimal 2v2 replay in the ballchasing ``GET /replays/{id}`` shape."""
    return {
        "id": "abc-123",
        "status": "ok",
        "date": "2026-09-01T20:15:00Z",
        "playlist_id": "ranked-doubles",
        "playlist_name": "Ranked Doubles",
        "team_size": 2,
        "duration": 300,
        "overtime": False,
        "map_name": "DFH Stadium",
        "blue": {
            "players": [make_player("Me", "111", goals=2, shots=3), make_player("Buddy", "222", platform="epic")],
            "stats": {"core": {"goals": 3}},
        },
        "orange": {
            "players": [make_player("Opp1", "333", saves=4), make_player("Opp2", "444")],
            "stats": {"core": {"goals": 1}},
        },
    }


@pytest.fixture(scope="session")
def demo_replays() -> list[dict[str, Any]]:
    return generate_replays(games=240, seed=11)


@pytest.fixture
def demo_matches(demo_replays):
    who = PlayerRef.parse(f"{DEMO_PLAYER['platform']}:{DEMO_PLAYER['id']}")
    return [m for m in (parse_replay(copy.deepcopy(r), who) for r in demo_replays) if m]
