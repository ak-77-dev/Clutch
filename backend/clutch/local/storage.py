"""Storage cleanup: keep the clips folder under the player's limits.

Two independent rules, both off by default: delete clips older than N days,
and delete the oldest clips while the folder is over N GB. Favorites are never
touched, and everything goes to the Recycle Bin, so a cleanup can be undone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CleanupPlan:
    clip_ids: list[int]
    bytes: int
    reasons: dict[int, str]

    def to_dict(self) -> dict[str, Any]:
        return {"count": len(self.clip_ids), "bytes": self.bytes, "clip_ids": self.clip_ids, "reasons": self.reasons}


def plan_cleanup(clips: list[dict[str, Any]], *, max_days: int = 0, max_gb: float = 0, now: float | None = None) -> CleanupPlan:
    """Which clips a cleanup would remove (pure: the caller decides whether to act)."""
    now = now or time.time()
    candidates = sorted((c for c in clips if not c["favorite"] and c.get("exists", True)), key=lambda c: c["created_at"])
    chosen: dict[int, str] = {}
    if max_days > 0:
        cutoff = now - max_days * 86400
        for c in candidates:
            if c["created_at"] < cutoff:
                chosen[c["id"]] = f"older than {max_days} days"
    if max_gb > 0:
        limit = max_gb * 1024**3
        total = sum(c["size"] or 0 for c in clips if c.get("exists", True) and c["id"] not in chosen)
        for c in candidates:  # oldest first
            if total <= limit:
                break
            if c["id"] in chosen:
                continue
            chosen[c["id"]] = f"over the {max_gb:g} GB limit"
            total -= c["size"] or 0
    sizes = {c["id"]: c["size"] or 0 for c in clips}
    ids = [c["id"] for c in candidates if c["id"] in chosen]
    return CleanupPlan(ids, sum(sizes[i] for i in ids), chosen)
