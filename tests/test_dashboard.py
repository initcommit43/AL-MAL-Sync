"""Tests for dashboard.py: fetch_dashboard_stats() against faked
AniList/MyAnimeList clients and oauth objects (no real network/OAuth), one
service's auth state or API error never affecting the other's result."""

from __future__ import annotations

from typing import Any

import pytest

from al_mal_sync import dashboard
from al_mal_sync.clients.anilist import AniListAPIError, AniListListEntry, AniListMedia
from al_mal_sync.clients.myanimelist import (
    MALAnime,
    MALAnimeListStatus,
    MALManga,
    MALMangaListStatus,
    MALUserAnimeEntry,
    MALUserMangaEntry,
    MyAnimeListAPIError,
)
from al_mal_sync.config import Config


class _FakeOAuth:
    def __init__(self, *, needs_init: bool) -> None:
        self.needs_init = needs_init


def _anilist_anime_entry(i: int) -> AniListListEntry:
    return AniListListEntry(
        id=i, status="CURRENT", score=7.0, progress=3,
        media=AniListMedia(id=i, episodes=12, duration=24),
    )


def _anilist_manga_entry(i: int) -> AniListListEntry:
    return AniListListEntry(
        id=i, status="COMPLETED", score=8.0, progress=10, progress_volumes=1,
        media=AniListMedia(id=i),
    )


def _mal_anime_entry(i: int) -> MALUserAnimeEntry:
    return MALUserAnimeEntry(
        anime=MALAnime(id=i),
        status=MALAnimeListStatus(status="watching", score=6, num_episodes_watched=3),
    )


def _mal_manga_entry(i: int) -> MALUserMangaEntry:
    return MALUserMangaEntry(
        manga=MALManga(id=i),
        status=MALMangaListStatus(
            status="completed", score=9, num_chapters_read=10, num_volumes_read=1
        ),
    )


class _FakeAniListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[AniListListEntry]:
        return [_anilist_anime_entry(i) for i in range(3)]

    def get_user_manga_list(self) -> list[AniListListEntry]:
        return [_anilist_manga_entry(i) for i in range(5)]

    def get_user_score_format(self) -> str:
        return "POINT_10"


class _FakeMyAnimeListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[MALUserAnimeEntry]:
        return [_mal_anime_entry(i) for i in range(4)]

    def get_user_manga_list(self) -> list[MALUserMangaEntry]:
        return [_mal_manga_entry(i) for i in range(4)]


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

        assert stats.anilist.authenticated is True
        assert stats.anilist.anime_count == 3
        assert stats.anilist.manga_count == 5
        assert stats.anilist.stats is not None
        assert stats.myanimelist.authenticated is True
        assert stats.myanimelist.anime_count == 4
        assert stats.myanimelist.manga_count == 4
        assert stats.myanimelist.stats is not None

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
