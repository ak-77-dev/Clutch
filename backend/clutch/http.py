"""Rate-limited JSON HTTP client shared by every game adapter."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body  # upstream JSON error payload, when there is one


class NotFound(ApiError):
    pass


class AuthFailed(ApiError):
    pass


class SlidingWindowLimiter:
    """Allows at most ``limit`` calls per ``window`` seconds for every (limit, window) pair.

    Riot dev keys, for example, are capped at 20 requests / 1 s AND 100 / 120 s.
    Thread-safe: the web server may run several requests at once.
    """

    def __init__(
        self,
        windows: list[tuple[int, float]],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.windows = [(limit, window, deque()) for limit, window in windows]
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                wait = 0.0
                for limit, window, calls in self.windows:
                    while calls and now - calls[0] >= window:
                        calls.popleft()
                    if len(calls) >= limit:
                        wait = max(wait, window - (now - calls[0]))
                if wait <= 0:
                    for _, _, calls in self.windows:
                        calls.append(now)
                    return
            self._sleep(wait)


class JsonClient:
    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        limiter: SlidingWindowLimiter | None = None,
        session: requests.Session | None = None,
        max_retries: int = 3,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "clutch/0.1", **(headers or {})})
        self.limiter = limiter
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            if self.limiter:
                self.limiter.acquire()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise ApiError(f"network error: {exc}") from exc
                self._sleep(2**attempt)
                continue
            if resp.status_code == 404:
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                raise NotFound("not found", 404, body)
            if resp.status_code in (401, 403):
                raise AuthFailed("API key rejected or expired", resp.status_code)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                # HenrikDev sends x-ratelimit-reset (seconds until the window resets) instead of Retry-After.
                hint = resp.headers.get("Retry-After") or (resp.status_code == 429 and resp.headers.get("x-ratelimit-reset"))
                try:
                    delay = float(hint or 2**attempt)
                except ValueError:
                    delay = 2**attempt
                self._sleep(min(delay, 60.0))
                continue
            if resp.status_code >= 400:
                raise ApiError(f"upstream error HTTP {resp.status_code}", resp.status_code)
            return resp.json()
        raise ApiError("retries exhausted")  # pragma: no cover
