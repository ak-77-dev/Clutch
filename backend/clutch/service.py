"""Application service: lookup, sync and read models for the API layer."""

from __future__ import annotations

import contextlib
from typing import Any

from clutch import analytics
from clutch.games.base import GameProvider, is_demo_query
from clutch.http import ApiError
from clutch.models import Match, Profile
from clutch.store import Store


def derived_ranks(matches: list[Match]) -> list[dict[str, Any]]:
    """Latest rank per mode, for games whose API has no rank endpoint (matches are newest-first)."""
    seen: dict[str, dict[str, Any]] = {}
    for m in matches:
        if m.rank_label and m.mode not in seen:
            seen[m.mode] = {
                "queue": m.mode,
                "label": m.rank_label,
                "value": m.rank_value,
                "tier": m.rank_label.split()[0].lower(),
                "lp": None,
                "wins": None,
                "losses": None,
            }
    return list(seen.values())


class UnknownGame(LookupError):
    pass


class NotConfigured(RuntimeError):
    pass


class Clutch:
    def __init__(self, store: Store, providers: list[GameProvider], *, sync_limit: int = 40) -> None:
        self.store = store
        self.providers = {p.meta.id: p for p in providers}
        self.sync_limit = sync_limit
        self._parsed: dict[tuple[str, str], tuple[int, list[Match]]] = {}

    def provider(self, game: str) -> GameProvider:
        try:
            return self.providers[game]
        except KeyError:
            raise UnknownGame(game) from None

    def games(self) -> list[dict[str, Any]]:
        return [p.info() for p in self.providers.values()]

    # ── lookup & sync ────────────────────────────────────────────────────────
    def lookup(self, game: str, query: str, *, refresh: bool = False) -> Profile:
        p = self.provider(game)
        if is_demo_query(query):
            return self._ensure_demo(p)
        cached = self.store.profile_by_query(game, query)
        if cached and not refresh:
            return cached
        if not p.configured():
            raise NotConfigured(f"{p.meta.name} needs an API key — see the README, or try the demo profile.")
        profile = p.resolve(query)
        self.store.save_profile(profile, query, profile.key)
        if not self.store.synced_at(game, profile.key):
            self.sync(game, profile.key)
        return profile

    def _ensure_demo(self, p: GameProvider) -> Profile:
        profile = p.demo_profile()
        if not self.store.profile(p.meta.id, profile.key):
            raws = p.demo_matches()
            self.store.save_profile(profile, "demo", profile.key)
            self.store.add_matches(p.meta.id, profile.key, ((p.match_id_of(r), p.match_date(r), r) for r in raws))
            self.store.mark_synced(p.meta.id, profile.key)
        return profile

    def sync(self, game: str, key: str) -> dict[str, Any]:
        p = self.provider(game)
        profile = self.store.profile(game, key)
        if profile is None:
            raise LookupError(key)
        if profile.demo:
            return {"new": 0, "failed": 0, "synced_at": self.store.synced_at(game, key), "demo": True}
        if not p.configured():
            raise NotConfigured(f"{p.meta.name} needs an API key")
        known = self.store.known_match_ids(game, key)
        new_ids = [mid for mid in p.list_match_ids(profile, self.sync_limit) if mid not in known]
        fetched, failed = [], 0
        for mid in new_ids:
            try:
                raw = p.fetch_match(profile, mid)
            except ApiError:
                failed += 1
                continue
            fetched.append((mid, p.match_date(raw), raw))
        self.store.add_matches(game, key, fetched)
        with contextlib.suppress(ApiError):  # rank refresh is best-effort
            self.store.save_profile(p.refresh_profile(profile))
        synced = self.store.mark_synced(game, key)
        return {"new": len(fetched), "failed": failed, "synced_at": synced, "demo": False}

    # ── read models ──────────────────────────────────────────────────────────
    def matches(self, game: str, key: str) -> list[Match]:
        """Parsed matches, newest first (memoized until new raw matches arrive)."""
        p = self.provider(game)
        profile = self.store.profile(game, key)
        if profile is None:
            raise LookupError(key)
        raws = self.store.raw_matches(game, key)
        cached = self._parsed.get((game, key))
        if cached and cached[0] == len(raws):
            return cached[1]
        parsed = [m for m in (p.parse(r, profile) for r in raws) if m is not None]
        parsed.sort(key=lambda m: analytics.parse_date(m.date), reverse=True)
        self._parsed[(game, key)] = (len(raws), parsed)
        return parsed

    def overview(self, game: str, key: str) -> dict[str, Any]:
        p = self.provider(game)
        profile = self.store.profile(game, key)
        if profile is None:
            raise LookupError(key)
        ms = self.matches(game, key)
        if not profile.ranks:
            profile.ranks = derived_ranks(ms)
        return {
            "game": p.info(),
            "profile": profile.to_dict(),
            "synced_at": self.store.synced_at(game, key),
            "overview": analytics.build_overview(p.meta, ms),
            "recent": [m.summary_dict() for m in ms[:10]],
        }

    def match_page(
        self, game: str, key: str, *, offset: int = 0, limit: int = 20, character: str | None = None, mode: str | None = None
    ) -> dict[str, Any]:
        ms = self.matches(game, key)
        if character:
            ms = [m for m in ms if m.character == character]
        if mode:
            ms = [m for m in ms if m.mode == mode]
        return {"total": len(ms), "offset": offset, "items": [m.summary_dict() for m in ms[offset : offset + limit]]}

    def match_detail(self, game: str, key: str, match_id: str) -> dict[str, Any]:
        for m in self.matches(game, key):
            if m.id == match_id:
                return m.to_dict()
        raise LookupError(match_id)
