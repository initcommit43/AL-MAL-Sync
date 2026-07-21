"""Tests for the AniList GraphQL client: list flattening, date handling, the
nil-date-omission rule, favourite-toggle validation, and error mapping (including
the stricter-than-Go check for GraphQL errors returned alongside HTTP 200)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from al_mal_sync.clients.anilist import (
    AniListAPIError,
    AniListClient,
    AniListDate,
    AniListNotFoundError,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self._responses[len(self.calls) - 1]


def _make_client(responses: list[_FakeResponse]) -> tuple[AniListClient, _FakeSession]:
    fake_oauth = SimpleNamespace(
        get_valid_token=lambda: SimpleNamespace(token_type="Bearer", access_token="tok")
    )
    client = AniListClient(fake_oauth, "someuser")  # type: ignore[arg-type]
    session = _FakeSession(responses)
    client.session = session  # type: ignore[assignment]
    return client, session


class TestAniListDate:
    def test_full_date_converts(self) -> None:
        assert AniListDate(2020, 6, 15).to_date() == date(2020, 6, 15)

    def test_partial_date_returns_none(self) -> None:
        assert AniListDate(2020, 6, None).to_date() is None

    def test_missing_or_empty_dict_returns_none(self) -> None:
        assert AniListDate.from_dict(None) is None
        assert AniListDate.from_dict({}) is None


class TestGetUserAnimeList:
    def test_flattens_groups_and_parses_entry(self) -> None:
        payload = {
            "data": {
                "MediaListCollection": {
                    "lists": [
                        {
                            "status": "CURRENT",
                            "entries": [
                                {
                                    "id": 1,
                                    "status": "CURRENT",
                                    "score": 8.0,
                                    "progress": 5,
                                    "startedAt": {"year": 2024, "month": 1, "day": 1},
                                    "completedAt": None,
                                    "media": {
                                        "id": 100,
                                        "idMal": 200,
                                        "title": {"romaji": "R", "english": "E", "native": "N"},
                                        "status": "RELEASING",
                                        "episodes": 12,
                                        "seasonYear": 2024,
                                        "isFavourite": True,
                                    },
                                }
                            ],
                        }
                    ]
                }
            }
        }
        client, _ = _make_client([_FakeResponse(200, payload)])

        entries = client.get_user_anime_list()

        assert len(entries) == 1
        assert entries[0].media.id == 100
        assert entries[0].started_at.to_date() == date(2024, 1, 1)
        assert entries[0].completed_at is None


class TestGetAuthenticatedUsername:
    def test_returns_viewer_name(self) -> None:
        payload = {"data": {"Viewer": {"name": "SomeUser"}}}
        client, session = _make_client([_FakeResponse(200, payload)])

        username = client.get_authenticated_username()

        assert username == "SomeUser"
        assert session.calls[0][1]["json"]["variables"] == {}


class TestUpdateAnimeEntry:
    def test_omits_dates_when_none(self) -> None:
        client, session = _make_client([_FakeResponse(200, {"data": {}})])

        client.update_anime_entry(100, "CURRENT", 5, 8)

        variables = session.calls[0][1]["json"]["variables"]
        assert "startedAt" not in variables
        assert "completedAt" not in variables

    def test_includes_dates_when_given(self) -> None:
        client, session = _make_client([_FakeResponse(200, {"data": {}})])

        client.update_anime_entry(
            100, "COMPLETED", 12, 9, started_at=date(2024, 1, 1), completed_at=date(2024, 2, 1)
        )

        variables = session.calls[0][1]["json"]["variables"]
        assert variables["startedAt"] == {"year": 2024, "month": 1, "day": 1}
        assert variables["completedAt"] == {"year": 2024, "month": 2, "day": 1}


class TestToggleFavourite:
    def test_requires_one_id(self) -> None:
        client, _ = _make_client([])
        with pytest.raises(ValueError, match="at least one"):
            client.toggle_favourite()

    def test_rejects_both_ids(self) -> None:
        client, _ = _make_client([])
        with pytest.raises(ValueError, match="only one"):
            client.toggle_favourite(anime_id=1, manga_id=2)


class TestErrorMapping:
    def test_non_ok_status_surfaces_graphql_message(self) -> None:
        client, _ = _make_client(
            [_FakeResponse(400, {"errors": [{"message": "Invalid media"}]})]
        )
        with pytest.raises(AniListAPIError, match="Invalid media"):
            client.get_anime_by_id(1)

    def test_http_200_with_errors_array_still_raises(self) -> None:
        # Deliberate deviation from the Go client, which only checks errors on
        # non-200 responses. AniList can return 200 with a populated errors array.
        client, _ = _make_client(
            [_FakeResponse(200, {"data": None, "errors": [{"message": "Rate limited"}]})]
        )
        with pytest.raises(AniListAPIError, match="Rate limited"):
            client.get_anime_by_id(1)

    def test_not_found_when_media_missing(self) -> None:
        client, _ = _make_client([_FakeResponse(200, {"data": {"Page": {"media": []}}})])
        with pytest.raises(AniListNotFoundError):
            client.get_anime_by_mal_id(999)
