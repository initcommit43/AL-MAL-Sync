"""Tests for dashboard.py: fetch_dashboard_stats() against faked
AniList/MyAnimeList clients and oauth objects (no real network/OAuth), one
service's auth state or API error never affecting the other's result."""

from __future__ import annotations

from typing import Any

import pytest

from al_mal_sync import dashboard
from al_mal_sync.clients.anilist import AniListAPIError
from al_mal_sync.clients.myanimelist import MyAnimeListAPIError
from al_mal_sync.config import Config


class _FakeOAuth:
    def __init__(self, *, needs_init: bool) -> None:
        self.needs_init = needs_init


class _FakeAniListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[Any]:
        return [object()] * 3

    def get_user_manga_list(self) -> list[Any]:
        return [object()] * 5


class _FakeMyAnimeListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[Any]:
        return [object()] * 4

    def get_user_manga_list(self) -> list[Any]:
        return [object()] * 4


class _ErroringAniListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[Any]:
        raise AniListAPIError("boom", status_code=500)


class _ErroringMyAnimeListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[Any]:
        raise MyAnimeListAPIError("boom", status_code=500)


def _config() -> Config:
    cfg = Config()
    cfg.anilist.username = "someuser"
    cfg.myanimelist.username = "someuser"
    return cfg


class TestFetchDashboardStats:
    def test_both_authenticated_returns_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=False))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )
        monkeypatch.setattr(dashboard, "AniListClient", _FakeAniListClient)
        monkeypatch.setattr(dashboard, "MyAnimeListClient", _FakeMyAnimeListClient)

        stats = dashboard.fetch_dashboard_stats(_config())

        assert stats.anilist == dashboard.PlatformStatus(authenticated=True, anime_count=3, manga_count=5)
        assert stats.myanimelist == dashboard.PlatformStatus(
            authenticated=True, anime_count=4, manga_count=4
        )

    def test_one_service_not_authenticated_does_not_block_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=True))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )
        monkeypatch.setattr(dashboard, "AniListClient", _FakeAniListClient)
        monkeypatch.setattr(dashboard, "MyAnimeListClient", _FakeMyAnimeListClient)

        stats = dashboard.fetch_dashboard_stats(_config())

        assert stats.anilist.authenticated is False
        assert stats.anilist.anime_count is None
        assert stats.myanimelist.authenticated is True
        assert stats.myanimelist.anime_count == 4

    def test_neither_authenticated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=True))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=True)
        )

        stats = dashboard.fetch_dashboard_stats(_config())

        assert stats.anilist == dashboard.PlatformStatus(authenticated=False)
        assert stats.myanimelist == dashboard.PlatformStatus(authenticated=False)

    def test_api_error_on_one_service_does_not_block_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=False))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )
        monkeypatch.setattr(dashboard, "AniListClient", _ErroringAniListClient)
        monkeypatch.setattr(dashboard, "MyAnimeListClient", _FakeMyAnimeListClient)

        stats = dashboard.fetch_dashboard_stats(_config())

        assert stats.anilist.authenticated is True
        assert stats.anilist.anime_count is None
        assert stats.anilist.error == "boom"
        assert stats.myanimelist.authenticated is True
        assert stats.myanimelist.anime_count == 4

    def test_mal_api_error_does_not_block_anilist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=False))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )
        monkeypatch.setattr(dashboard, "AniListClient", _FakeAniListClient)
        monkeypatch.setattr(dashboard, "MyAnimeListClient", _ErroringMyAnimeListClient)

        stats = dashboard.fetch_dashboard_stats(_config())

        assert stats.anilist.authenticated is True
        assert stats.anilist.anime_count == 3
        assert stats.myanimelist.authenticated is True
        assert stats.myanimelist.error == "boom"


class TestUnconfiguredUsername:
    """A token can exist (logged in) with no username saved in Settings yet
    -- MAL's list endpoint 404s on an empty username path segment, and
    AniList's GraphQL resolver 404s on an unknown user. Both should surface
    as a clear "check Settings" message instead of a raw API error, and
    neither client should even be constructed (no point making a request
    that's guaranteed to fail)."""

    def test_anilist_username_empty_short_circuits_without_calling_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=False))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )

        def _boom(*a: Any, **kw: Any) -> Any:
            raise AssertionError("AniListClient should not be constructed with no username set")

        monkeypatch.setattr(dashboard, "AniListClient", _boom)
        monkeypatch.setattr(dashboard, "MyAnimeListClient", _FakeMyAnimeListClient)

        cfg = _config()
        cfg.anilist.username = ""
        stats = dashboard.fetch_dashboard_stats(cfg)

        assert stats.anilist.authenticated is True
        assert stats.anilist.anime_count is None
        assert "username" in (stats.anilist.error or "")

    def test_myanimelist_username_empty_short_circuits_without_calling_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dashboard, "create_anilist_oauth", lambda config: _FakeOAuth(needs_init=False))
        monkeypatch.setattr(
            dashboard, "create_myanimelist_oauth", lambda config: _FakeOAuth(needs_init=False)
        )
        monkeypatch.setattr(dashboard, "AniListClient", _FakeAniListClient)

        def _boom(*a: Any, **kw: Any) -> Any:
            raise AssertionError("MyAnimeListClient should not be constructed with no username set")

        monkeypatch.setattr(dashboard, "MyAnimeListClient", _boom)

        cfg = _config()
        cfg.myanimelist.username = ""
        stats = dashboard.fetch_dashboard_stats(cfg)

        assert stats.myanimelist.authenticated is True
        assert stats.myanimelist.anime_count is None
        assert "username" in (stats.myanimelist.error or "")
