from typing import Any

import pytest
import requests

from rlstats.client import AuthError, BallchasingClient, BallchasingError, RateLimiter
from rlstats.store import ReplayStore
from rlstats.sync import sync


class FakeResponse:
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, Any]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return BallchasingClient("key", session=FakeSession(responses), per_second=0, sleep=sleeps.append), sleeps


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(2, clock=lambda: now[0], sleep=sleep)
    rl.wait()
    rl.wait()
    rl.wait()
    assert slept == [0.5, 0.5]


def test_retries_429_honoring_retry_after():
    c, sleeps = client([FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, {"id": "x"})])
    assert c.replay("x") == {"id": "x"}
    assert sleeps == [3.0]


def test_retries_network_errors_then_gives_up():
    c, sleeps = client([requests.ConnectionError("boom")] * 5)
    with pytest.raises(BallchasingError):
        c.replay("x")
    assert sleeps == [1, 2, 4, 8]


def test_auth_error_is_not_retried():
    c, sleeps = client([FakeResponse(401)])
    with pytest.raises(AuthError):
        c.whoami()
    assert sleeps == []


def test_missing_key_is_a_friendly_error():
    with pytest.raises(AuthError):
        BallchasingClient("")


def test_pagination_follows_next():
    c, _ = client(
        [
            FakeResponse(200, {"list": [{"id": "a"}, {"id": "b"}], "next": "https://ballchasing.com/api/replays?after=b"}),
            FakeResponse(200, {"list": [{"id": "c"}]}),
        ]
    )
    assert [r["id"] for r in c.iter_replays(uploader="me")] == ["a", "b", "c"]
    calls = c.session.calls
    assert calls[0][1]["uploader"] == "me" and calls[1] == ("https://ballchasing.com/api/replays?after=b", None)


class FakeSource:
    def __init__(self, ids, details):
        self.ids, self.details, self.fetched, self.filters = ids, details, [], None

    def iter_replays(self, **filters):
        self.filters = filters
        for i in self.ids:
            yield {"id": i}

    def replay(self, rid):
        self.fetched.append(rid)
        d = self.details.get(rid, {"id": rid, "status": "ok", "date": f"2026-09-0{len(self.fetched)}T00:00:00Z"})
        if isinstance(d, Exception):
            raise d
        return d


def test_sync_is_incremental(tmp_path):
    with ReplayStore(tmp_path / "t.db") as store:
        store.upsert({"id": "old1", "date": "2026-01-01"})
        store.upsert({"id": "old2", "date": "2026-01-02"})
        src = FakeSource(["new1", "new2", "old2", "old1", "older"], {})
        result = sync(src, store, stop_after_known=2, log=lambda _: None)
        assert src.fetched == ["new1", "new2"]  # stopped at 2 known replays, never reached "older"
        assert (result.downloaded, result.already_had) == (2, 2)
        assert store.count() == 4
        assert store.get_meta("last_sync")
        assert src.filters == {"uploader": "me"}


def test_sync_skips_pending_and_records_failures(tmp_path):
    with ReplayStore(tmp_path / "t.db") as store:
        src = FakeSource(["p", "bad", "ok"], {"p": {"id": "p", "status": "pending"}, "bad": BallchasingError("500")})
        result = sync(src, store, player_filter="steam:1", log=lambda _: None)
        assert (result.pending, result.failed, result.downloaded) == (1, ["bad"], 1)
        assert store.known_ids() == {"ok"}
        assert src.filters == {"player-id": "steam:1"}


def test_store_upsert_replaces(tmp_path):
    with ReplayStore(tmp_path / "t.db") as store:
        store.upsert({"id": "a", "date": "2026-01-01", "v": 1})
        store.upsert({"id": "a", "date": "2026-01-01", "v": 2})
        assert [r["v"] for r in store.iter_raw()] == [2]
