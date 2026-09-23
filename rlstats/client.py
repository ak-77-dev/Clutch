"""Minimal ballchasing.com API client.

- Client-side rate limiting (free tier allows ~2 requests/second).
- Retries 429 / 5xx / connection errors with exponential backoff, honoring
  ``Retry-After``.
- Follows the list endpoint's ``next`` cursor for pagination.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

BASE_URL = "https://ballchasing.com/api"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class BallchasingError(RuntimeError):
    pass


class AuthError(BallchasingError):
    pass


class RateLimiter:
    """Enforces a minimum interval between calls."""

    def __init__(
        self, per_second: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._clock, self._sleep = clock, sleep
        self._next = 0.0

    def wait(self) -> None:
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


class BallchasingClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        per_second: float = 2.0,
        max_retries: int = 4,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise AuthError("BALLCHASING_API_KEY is not set — get a token at https://ballchasing.com/upload")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": api_key, "User-Agent": "rlstats/2.0"})
        self.limiter = RateLimiter(per_second, sleep=sleep)
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not url.startswith("http"):
            url = f"{BASE_URL}{url}"
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise BallchasingError(f"network error calling {url}: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code in (401, 403):
                raise AuthError("ballchasing rejected the API key (401/403) — check BALLCHASING_API_KEY")
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2**attempt
                except ValueError:
                    delay = 2**attempt
                self._sleep(min(delay, 60.0))
                continue
            if resp.status_code >= 400:
                raise BallchasingError(f"GET {url} failed: HTTP {resp.status_code}")
            return resp.json()
        raise BallchasingError(f"GET {url} failed after {self.max_retries} retries")  # pragma: no cover

    def whoami(self) -> dict[str, Any]:
        """``GET /`` — the account that owns the API key (steam_id, name, type)."""
        return self._get("/")

    def iter_replays(self, **filters: Any) -> Iterator[dict[str, Any]]:
        """Replay summaries newest-first, following pagination until exhausted."""
        params: dict[str, Any] | None = {"count": 200, "sort-by": "replay-date", "sort-dir": "desc", **filters}
        url = "/replays"
        while url:
            page = self._get(url, params)
            yield from page.get("list", []) or []
            url, params = page.get("next") or "", None  # `next` already carries the query

    def replay(self, replay_id: str) -> dict[str, Any]:
        return self._get(f"/replays/{replay_id}")
