"""Tests for the Jikan API client: caching, rate limiting, and the
title-matching helpers strategies.py's JikanApiStrategy depends on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from al_mal_sync.mapping.jikan_api import (
    JikanApiError,
    JikanCache,
    JikanClient,
    JikanMangaData,
    find_best_jikan_match,
    match_jikan_manga_to_source,
    search_titles_for_jikan,
)


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
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._responses[len(self.calls) - 1]


def _make_client(responses: list[_FakeResponse], cache_dir: str) -> tuple[JikanClient, _FakeSession]:
    client = JikanClient(cache_dir)
    session = _FakeSession(responses)
    client.session = session  # type: ignore[assignment]
    return client, session


class TestJikanCache:
    def test_manga_roundtrip(self, tmp_path: Path) -> None:
        cache = JikanCache(str(tmp_path))
        cache.set_manga(1, JikanMangaData(mal_id=1, title="Berserk"))
        data, found = cache.get_manga(1)
        assert found is True
        assert data is not None
        assert data.title == "Berserk"

    def test_manga_negative_cache(self, tmp_path: Path) -> None:
        cache = JikanCache(str(tmp_path))
        cache.set_manga(1, None)
        data, found = cache.get_manga(1)
        assert found is True
        assert data is None

    def test_search_key_is_normalized(self, tmp_path: Path) -> None:
        cache = JikanCache(str(tmp_path))
        cache.set_search("Berserk!", [JikanMangaData(mal_id=1)])
        assert cache.get_search("berserk") is not None  # different case/punctuation, same key

    def test_expired_entry_is_miss(self, tmp_path: Path) -> None:
        cache = JikanCache(str(tmp_path), max_age_seconds=1)
        cache.set_manga(1, JikanMangaData(mal_id=1))
        key = next(iter(cache._entries))
        cache._entries[key]["cached_at"] = 0  # ancient
        _, found = cache.get_manga(1)
        assert found is False


class TestJikanClientManga:
    def test_cache_hit_skips_api_call(self, tmp_path: Path) -> None:
        client, session = _make_client([], str(tmp_path))
        client.cache.set_manga(1, JikanMangaData(mal_id=1, title="Cached"))

        result = client.get_manga_by_mal_id(1)

        assert result is not None
        assert result.title == "Cached"
        assert session.calls == []

    def test_404_caches_negative_result(self, tmp_path: Path) -> None:
        client, session = _make_client([_FakeResponse(404)], str(tmp_path))

        first = client.get_manga_by_mal_id(1)
        second = client.get_manga_by_mal_id(1)

        assert first is None
        assert second is None
        assert len(session.calls) == 1

    def test_non_positive_id_returns_none_without_request(self, tmp_path: Path) -> None:
        client, session = _make_client([], str(tmp_path))
        assert client.get_manga_by_mal_id(0) is None
        assert session.calls == []

    def test_rate_limit_sleeps_on_rapid_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        monkeypatch.setattr(
            "al_mal_sync.mapping.jikan_api.time.sleep", lambda s: sleeps.append(s)
        )
        client = JikanClient(str(tmp_path))

        client._rate_limit()
        client._rate_limit()

        assert len(sleeps) == 1
        assert sleeps[0] > 0


class TestJikanClientFavorites:
    def test_parses_anime_and_manga_ids(self, tmp_path: Path) -> None:
        client, _ = _make_client(
            [
                _FakeResponse(
                    200,
                    {
                        "data": {
                            "anime": [{"mal_id": 1, "title": "A"}],
                            "manga": [{"mal_id": 2, "title": "B"}],
                        }
                    },
                )
            ],
            str(tmp_path),
        )

        anime_ids, manga_ids = client.get_user_favorites("someuser")

        assert anime_ids == {1}
        assert manga_ids == {2}

    def test_404_raises_jikan_api_error(self, tmp_path: Path) -> None:
        client, _ = _make_client([_FakeResponse(404)], str(tmp_path))
        with pytest.raises(JikanApiError):
            client.get_user_favorites("privateuser")

    def test_empty_username_raises_value_error(self, tmp_path: Path) -> None:
        client, _ = _make_client([], str(tmp_path))
        with pytest.raises(ValueError, match="username"):
            client.get_user_favorites("")


class TestSearchTitlesForJikan:
    def test_prefers_romaji_first(self) -> None:
        assert search_titles_for_jikan("English", "Romaji") == ["Romaji", "English"]

    def test_skips_empty_titles(self) -> None:
        assert search_titles_for_jikan("", "Romaji") == ["Romaji"]

    def test_dedupes_by_normalized_form(self) -> None:
        assert search_titles_for_jikan("Same Title", "Same Title!") == ["Same Title!"]


class TestMatchJikanMangaToSource:
    def test_matches_on_synonym(self) -> None:
        jikan_data = JikanMangaData(
            title="Main", title_english="", title_japanese="", title_synonyms=["Alt Name"]
        )
        assert match_jikan_manga_to_source(jikan_data, "Alt Name", "", "") is True

    def test_cross_matches_source_english_against_jikan_main_title(self) -> None:
        jikan_data = JikanMangaData(title="Berserk", title_english="", title_japanese="")
        assert match_jikan_manga_to_source(jikan_data, "Berserk", "", "") is True

    def test_no_match_for_unrelated_titles(self) -> None:
        jikan_data = JikanMangaData(title="Naruto", title_english="Naruto")
        assert match_jikan_manga_to_source(jikan_data, "One Piece", "", "One Piece") is False


class TestFindBestJikanMatch:
    def test_returns_first_matching_id(self) -> None:
        results = [
            JikanMangaData(mal_id=1, title="Wrong"),
            JikanMangaData(mal_id=2, title="Berserk"),
        ]
        assert find_best_jikan_match(results, "Berserk", "", "") == 2

    def test_returns_zero_when_no_match(self) -> None:
        results = [JikanMangaData(mal_id=1, title="Wrong")]
        assert find_best_jikan_match(results, "Berserk", "", "") == 0
