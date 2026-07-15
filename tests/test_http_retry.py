"""Tests for HTTP retry/backoff: exponential backoff math, Retry-After handling,
and the retry-vs-raise decision for transient failures."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from al_mal_sync.http_retry import (
    ExponentialBackoff,
    HTTPRetryExhaustedError,
    _parse_retry_after,
    request_with_retry,
)


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url))
        item = self._items[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item


class TestExponentialBackoff:
    def test_attempt_zero_is_zero(self) -> None:
        assert ExponentialBackoff().duration(0) == 0.0

    def test_grows_with_multiplier(self) -> None:
        backoff = ExponentialBackoff(initial_interval=1.0, multiplier=2.0, max_interval=100.0)
        assert backoff.duration(1) == 1.0
        assert backoff.duration(2) == 2.0
        assert backoff.duration(3) == 4.0

    def test_caps_at_max_interval(self) -> None:
        backoff = ExponentialBackoff(initial_interval=1.0, multiplier=2.0, max_interval=3.0)
        assert backoff.duration(10) == 3.0


class TestParseRetryAfter:
    def test_delay_seconds(self) -> None:
        assert _parse_retry_after("5") == 5.0

    def test_zero_is_invalid(self) -> None:
        assert _parse_retry_after("0") is None

    def test_garbage_value_returns_none(self) -> None:
        assert _parse_retry_after("not-a-date") is None

    def test_http_date_in_past_returns_none(self) -> None:
        assert _parse_retry_after("Mon, 01 Jan 1990 00:00:00 GMT") is None


class TestRequestWithRetry:
    def test_success_on_first_try_no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: sleeps.append(s))
        response = _FakeResponse(200)
        session = _FakeSession([response])

        result = request_with_retry(session, "GET", "http://x")

        assert result is response
        assert sleeps == []
        assert len(session.calls) == 1

    def test_non_retryable_status_returns_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: None)
        response = _FakeResponse(404)
        session = _FakeSession([response])

        result = request_with_retry(session, "GET", "http://x")

        assert result is response
        assert len(session.calls) == 1

    def test_retries_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: None)
        ok = _FakeResponse(200)
        session = _FakeSession([_FakeResponse(503), _FakeResponse(503), ok])

        result = request_with_retry(session, "GET", "http://x", max_retries=3)

        assert result is ok
        assert len(session.calls) == 3

    def test_exhausts_retries_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: None)
        session = _FakeSession([_FakeResponse(503)] * 3)

        with pytest.raises(HTTPRetryExhaustedError):
            request_with_retry(session, "GET", "http://x", max_retries=2)

        assert len(session.calls) == 3  # initial attempt + 2 retries

    def test_connection_error_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: None)
        ok = _FakeResponse(200)
        session = _FakeSession([requests.ConnectionError("refused"), ok])

        result = request_with_retry(session, "GET", "http://x", max_retries=2)

        assert result is ok

    def test_persistent_connection_error_reraises_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: None)
        session = _FakeSession([requests.ConnectionError("refused")] * 2)

        with pytest.raises(requests.ConnectionError):
            request_with_retry(session, "GET", "http://x", max_retries=1)

    def test_retry_after_header_overrides_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        waits: list[float] = []
        monkeypatch.setattr("al_mal_sync.http_retry.time.sleep", lambda s: waits.append(s))
        ok = _FakeResponse(200)
        rate_limited = _FakeResponse(429, headers={"Retry-After": "7"})
        session = _FakeSession([rate_limited, ok])

        request_with_retry(session, "GET", "http://x", max_retries=1)

        assert waits == [7.0]
