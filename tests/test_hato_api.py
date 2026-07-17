"""Tests for the Hato API client: cache hit/miss/negative-cache behavior and
max-age expiry.

Max-age is worth its own tests specifically because the reference Go tool
exposes a HATO_API_CACHE_MAX_AGE config option that its own HatoCache never
actually enforces (entries are kept forever regardless). This port fixes that,
so the expiry tests double as regression protection for the fix itself.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from al_mal_sync.http_retry import HTTPRetryExhaustedError
from al_mal_sync.mapping.hato_api import HatoApiClient, HatoCache, HatoMappingData


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        return self._responses[len(self.calls) - 1]


def _make_client(
    responses: list[_FakeResponse], cache_dir: str
) -> tuple[HatoApiClient, _FakeSession]:
    client = HatoApiClient(cache_dir=cache_dir)
    session = _FakeSession(responses)
    client.session = session  # type: ignore[assignment]
    return client, session


class TestHatoCache:
    def test_set_then_get_roundtrip(self, tmp_path: Path) -> None:
        cache = HatoCache(str(tmp_path))
        cache.set("mal", "anime", 1, HatoMappingData(anilist_id=100))
        result = cache.get("mal", "anime", 1)
        assert result is not None
        assert result.anilist_id == 100

    def test_expired_entry_treated_as_miss(self, tmp_path: Path) -> None:
        cache = HatoCache(str(tmp_path), max_age_seconds=1)
        cache.set("mal", "anime", 1, HatoMappingData(anilist_id=100))
        key = next(iter(cache._entries))
        cache._entries[key]["cached_at"] = time.time() - 10  # backdate past max age
        assert cache.get("mal", "anime", 1) is None

    def test_zero_max_age_never_expires(self, tmp_path: Path) -> None:
        cache = HatoCache(str(tmp_path), max_age_seconds=0)
        cache.set("mal", "anime", 1, HatoMappingData(anilist_id=100))
        key = next(iter(cache._entries))
        cache._entries[key]["cached_at"] = 0
        assert cache.get("mal", "anime", 1) is not None

    def test_save_and_reload_persists_entries(self, tmp_path: Path) -> None:
        cache = HatoCache(str(tmp_path))
        cache.set("anilist", "manga", 5, HatoMappingData(mal_id=50))
        cache.save()

        reloaded = HatoCache(str(tmp_path))
        result = reloaded.get("anilist", "manga", 5)
        assert result is not None
        assert result.mal_id == 50


class TestHatoApiClientLookup:
    def test_cache_hit_skips_api_call(self, tmp_path: Path) -> None:
        client, session = _make_client([], str(tmp_path))
        client.cache.set("mal", "anime", 1, HatoMappingData(anilist_id=100))

        assert client.get_anilist_id(1, "anime") == 100
        assert session.calls == []

    def test_cache_miss_calls_api_and_populates_cache(self, tmp_path: Path) -> None:
        client, session = _make_client(
            [_FakeResponse(200, {"data": {"anilist_id": 100}})], str(tmp_path)
        )

        result = client.get_anilist_id(1, "anime")

        assert result == 100
        assert len(session.calls) == 1
        cached = client.cache.get("mal", "anime", 1)
        assert cached is not None
        assert cached.anilist_id == 100

    def test_404_caches_negative_result(self, tmp_path: Path) -> None:
        client, session = _make_client(
            [_FakeResponse(404), _FakeResponse(200, {"data": {"anilist_id": 999}})], str(tmp_path)
        )

        first = client.get_anilist_id(1, "anime")
        second = client.get_anilist_id(1, "anime")  # must be served from the negative cache

        assert first is None
        assert second is None
        assert len(session.calls) == 1

    def test_disabled_cache_still_works(self) -> None:
        client = HatoApiClient(cache_dir=None)
        session = _FakeSession([_FakeResponse(200, {"data": {"mal_id": 7}})])
        client.session = session  # type: ignore[assignment]

        assert client.get_mal_id(1, "manga") == 7
        assert client.cache is None

    def test_request_exception_is_non_fatal(self) -> None:
        """A flaky Hato (timeout, connection error, or the retry wrapper's
        own HTTPRetryExhaustedError once max_retries is exhausted) must not
        crash the whole sync run -- Hato is an optional fallback strategy,
        not a hard dependency. Regression test for a real failure hit during
        a live sync run against the actual Hato API."""

        class _RaisingSession:
            def get(self, url: str, **kwargs: Any) -> Any:
                raise HTTPRetryExhaustedError("max retries exhausted")

        client = HatoApiClient(cache_dir=None)
        client.session = _RaisingSession()  # type: ignore[assignment]

        assert client.get_anilist_id(1, "anime") is None
        assert client.get_mal_id(1, "anime") is None
