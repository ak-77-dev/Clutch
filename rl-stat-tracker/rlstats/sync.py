"""Incremental sync: download only replays the local store hasn't seen."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from rlstats.client import BallchasingError
from rlstats.store import ReplayStore


class ReplaySource(Protocol):
    def iter_replays(self, **filters: Any): ...  # pragma: no cover
    def replay(self, replay_id: str) -> dict[str, Any]: ...  # pragma: no cover


@dataclass
class SyncResult:
    listed: int = 0
    downloaded: int = 0
    already_had: int = 0
    pending: int = 0
    failed: list[str] = field(default_factory=list)


def sync(
    client: ReplaySource,
    store: ReplayStore,
    *,
    player_filter: str | None = None,
    max_new: int = 500,
    full: bool = False,
    stop_after_known: int = 25,
    log: Callable[[str], None] = print,
) -> SyncResult:
    """Pull new replays into ``store``.

    Lists newest-first; once ``stop_after_known`` consecutive replays are
    already stored, everything older is assumed synced (skipped with ``full``).
    ``player_filter`` (``platform:id``) pulls every public replay featuring the
    player; otherwise only replays uploaded by the API key's account.
    """
    filters = {"player-id": player_filter} if player_filter else {"uploader": "me"}
    known = store.known_ids()
    result = SyncResult()
    consecutive_known = 0
    to_fetch: list[str] = []

    for summary in client.iter_replays(**filters):
        result.listed += 1
        rid = str(summary["id"])
        if rid in known:
            result.already_had += 1
            consecutive_known += 1
            if not full and consecutive_known >= stop_after_known:
                break
            continue
        consecutive_known = 0
        to_fetch.append(rid)
        if len(to_fetch) >= max_new:
            break

    for i, rid in enumerate(to_fetch, 1):
        try:
            detail = client.replay(rid)
        except BallchasingError as exc:
            log(f"  [{i}/{len(to_fetch)}] {rid}: failed ({exc})")
            result.failed.append(rid)
            continue
        if detail.get("status") not in (None, "ok"):
            # Still processing on ballchasing's side — pick it up next sync.
            result.pending += 1
            continue
        store.upsert(detail)
        result.downloaded += 1
        log(f"  [{i}/{len(to_fetch)}] {detail.get('playlist_name', '?')} · {detail.get('date', '')[:16]}")

    store.set_meta("last_sync", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return result
