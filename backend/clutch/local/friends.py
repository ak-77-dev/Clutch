"""Friends feed: follow friends' public stats profiles.

Clutch has no server, so friends aren't accounts on a Clutch network: a friend
is someone's public profile in a game Clutch tracks (Riot ID, Steam account,
...). Their recent matches and rank come from the same APIs as your own stats,
refreshed in the background, and the feed shows what they've been playing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

REFRESH_S = 15 * 60


class FriendStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            self.friends: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.friends = []

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.friends, indent=1), encoding="utf-8")

    def add(self, game: str, key: str, name: str) -> dict[str, Any]:
        with self._lock:
            existing = next((f for f in self.friends if f["game"] == game and f["key"] == key), None)
            if existing:
                return existing
            friend = {"game": game, "key": key, "name": name, "added_at": time.time(), "synced_at": 0.0}
            self.friends.append(friend)
            self._save()
            return friend

    def remove(self, game: str, key: str) -> None:
        with self._lock:
            self.friends = [f for f in self.friends if not (f["game"] == game and f["key"] == key)]
            self._save()

    def touched(self, game: str, key: str) -> None:
        with self._lock:
            for f in self.friends:
                if f["game"] == game and f["key"] == key:
                    f["synced_at"] = time.time()
            self._save()

    def due(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        return [f for f in self.friends if now - f.get("synced_at", 0) > REFRESH_S]


def feed(friends: list[dict[str, Any]], stats: Any, limit: int = 30) -> dict[str, Any]:
    """Newest matches across friends, plus a card per friend (rank, last played, recent record)."""
    cards, items = [], []
    for f in friends:
        try:
            ov = stats.overview(f["game"], f["key"])
        except Exception:
            cards.append({**f, "error": True})
            continue
        profile, recent = ov["profile"], ov["recent"]
        record = [m["result"] for m in recent[:10]]
        cards.append(
            {
                **f,
                "name": profile["name"],
                "tag": profile.get("tag"),
                "icon": profile.get("icon"),
                "rank": (profile.get("ranks") or [{}])[0].get("label"),
                "last_played": recent[0]["date"] if recent else None,
                "wins": record.count("win"),
                "losses": record.count("loss"),
                "demo": profile.get("demo", False),
            }
        )
        for m in recent[:5]:
            items.append({"friend": f["name"], "friend_key": f["key"], "game": f["game"], "icon": profile.get("icon"), **m})
    items.sort(key=lambda m: m["date"], reverse=True)
    cards.sort(key=lambda c: c.get("last_played") or "", reverse=True)
    return {"cards": cards, "items": items[:limit]}
