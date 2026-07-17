"""Tests for the MyAnimeList REST client: pagination, update field-building,
and error mapping. Dataclass field mapping is only spot-checked, not exhaustively
tested field-by-field, since it's plain dict.get() with low regression risk."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from al_mal_sync.clients.myanimelist import (
    MALUserAnimeEntry,
    MyAnimeListAPIError,
    MyAnimeListClient,
    MyAnimeListNotFoundError,
    _parse_next_offset,
)


class _FakeResponse:
    def __init__(
        self, status_code: int, payload: dict[str, Any] | None = None, url: str = "http://x"
    ) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.url = url
        self._payload = payload
        self.content = b"{}" if payload is not None else b""
        self.text = str(payload) if payload is not None else ""

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url, kwargs))
        return self._responses[len(self.calls) - 1]

    def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("PATCH", url, kwargs))
        return self._responses[len(self.calls) - 1]


def _make_client(responses: list[_FakeResponse]) -> tuple[MyAnimeListClient, _FakeSession]:
    fake_oauth = SimpleNamespace(
        get_valid_token=lambda: SimpleNamespace(token_type="Bearer", access_token="tok")
    )
    client = MyAnimeListClient(fake_oauth, "someuser")  # type: ignore[arg-type]
    session = _FakeSession(responses)
    client.session = session  # type: ignore[assignment]
    return client, session


class TestParseNextOffset:
    def test_no_next_returns_none(self) -> None:
        assert _parse_next_offset({}) is None

    def test_extracts_offset_from_next_url(self) -> None:
        paging = {"next": "https://api.myanimelist.net/v2/users/x/animelist?offset=100&limit=100"}
        assert _parse_next_offset(paging) == 100

    def test_malformed_offset_returns_none(self) -> None:
        paging = {"next": "https://api.myanimelist.net/v2/users/x/animelist?offset=abc"}
        assert _parse_next_offset(paging) is None


class TestGetUserAnimeList:
    def test_paginates_across_pages(self) -> None:
        page1 = _FakeResponse(
            200,
            {
                "data": [{"node": {"id": 1, "title": "A"}, "list_status": {"status": "watching"}}],
                "paging": {"next": "https://x/?offset=100"},
            },
        )
        page2 = _FakeResponse(
            200,
            {
                "data": [{"node": {"id": 2, "title": "B"}, "list_status": {"status": "completed"}}],
                "paging": {},
            },
        )
        client, session = _make_client([page1, page2])

        entries = client.get_user_anime_list()

        assert [e.anime.id for e in entries] == [1, 2]
        # second call must have followed the offset from page1's paging.next
        assert session.calls[1][2]["params"]["offset"] == 100

    def test_parses_nested_season_and_status(self) -> None:
        entry = MALUserAnimeEntry.from_dict(
            {
                "node": {
                    "id": 5,
                    "title": "Show",
                    "num_episodes": 12,
                    "start_season": {"year": 2020, "season": "fall"},
                },
                "list_status": {"status": "watching", "num_episodes_watched": 3},
            }
        )
        assert entry.anime.start_season_year == 2020
        assert entry.anime.start_season == "fall"
        assert entry.status.num_episodes_watched == 3


class TestUpdateAnime:
    def test_sends_expected_form_fields(self) -> None:
        response = _FakeResponse(200, {"status": "watching", "score": 8})
        client, session = _make_client([response])

        client.update_anime(
            42,
            status="watching",
            score=8,
            num_watched_episodes=5,
            start_date=date(2024, 1, 15),
        )

        sent = session.calls[0][2]["data"]
        assert sent == {
            "status": "watching",
            "score": "8",
            "num_watched_episodes": "5",
            "start_date": "2024-01-15",
        }

    def test_no_fields_raises(self) -> None:
        client, _ = _make_client([])
        with pytest.raises(ValueError, match="no fields"):
            client.update_anime(42)

    def test_is_rewatching_encodes_as_lowercase_string(self) -> None:
        response = _FakeResponse(200, {"status": "watching"})
        client, session = _make_client([response])

        client.update_anime(42, is_rewatching=True)

        assert session.calls[0][2]["data"] == {"is_rewatching": "true"}

    def test_is_rewatching_false_is_still_sent(self) -> None:
        response = _FakeResponse(200, {"status": "watching"})
        client, session = _make_client([response])

        client.update_anime(42, is_rewatching=False)

        assert session.calls[0][2]["data"] == {"is_rewatching": "false"}


class TestUpdateManga:
    def test_sends_expected_form_fields(self) -> None:
        response = _FakeResponse(200, {"status": "reading", "score": 6})
        client, session = _make_client([response])

        client.update_manga(
            7,
            status="reading",
            score=6,
            num_chapters_read=10,
            num_volumes_read=1,
            is_rereading=True,
        )

        sent = session.calls[0][2]["data"]
        assert sent == {
            "status": "reading",
            "score": "6",
            "num_chapters_read": "10",
            "num_volumes_read": "1",
            "is_rereading": "true",
        }

    def test_no_fields_raises(self) -> None:
        client, _ = _make_client([])
        with pytest.raises(ValueError, match="no fields"):
            client.update_manga(7)


class TestErrorMapping:
    def test_404_raises_not_found(self) -> None:
        client, _ = _make_client([_FakeResponse(404, url="http://x/anime/999")])
        with pytest.raises(MyAnimeListNotFoundError):
            client.get_anime_by_id(999)

    def test_error_body_message_is_surfaced(self) -> None:
        client, _ = _make_client(
            [_FakeResponse(400, {"message": "invalid status value", "error": "bad_request"})]
        )
        with pytest.raises(MyAnimeListAPIError, match="invalid status value"):
            client.get_anime_by_id(1)

    def test_rejects_non_positive_id(self) -> None:
        client, _ = _make_client([])
        with pytest.raises(ValueError):
            client.get_anime_by_id(0)
