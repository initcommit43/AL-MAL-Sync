"""Tests for the ARM API client: ID lookup mapping and error handling."""

from __future__ import annotations

from typing import Any

from al_mal_sync.mapping.arm_api import ArmApiClient


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
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


def _make_client(responses: list[_FakeResponse]) -> tuple[ArmApiClient, _FakeSession]:
    client = ArmApiClient()
    session = _FakeSession(responses)
    client.session = session  # type: ignore[assignment]
    return client, session


class TestGetAniListId:
    def test_found(self) -> None:
        client, session = _make_client([_FakeResponse(200, {"anilist": 100})])
        assert client.get_anilist_id(1) == 100
        assert session.calls[0]["params"] == {"source": "myanimelist", "id": 1, "include": "anilist"}

    def test_null_field_treated_as_not_found(self) -> None:
        client, _ = _make_client([_FakeResponse(200, {"anilist": None})])
        assert client.get_anilist_id(1) is None

    def test_404_treated_as_not_found(self) -> None:
        client, _ = _make_client([_FakeResponse(404)])
        assert client.get_anilist_id(1) is None

    def test_server_error_treated_as_not_found(self) -> None:
        client, _ = _make_client([_FakeResponse(500)])
        assert client.get_anilist_id(1) is None


class TestGetMalId:
    def test_found(self) -> None:
        client, session = _make_client([_FakeResponse(200, {"myanimelist": 42})])
        assert client.get_mal_id(999) == 42
        assert session.calls[0]["params"]["source"] == "anilist"
