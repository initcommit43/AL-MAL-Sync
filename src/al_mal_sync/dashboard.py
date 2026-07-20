"""Live per-platform list-size snapshot for the GUI Dashboard: "how many
entries does each platform have, and how big is the gap". Reuses the same
OAuth/client construction as sync/runner.py, but only calls
get_user_anime_list()/get_user_manga_list() for their length -- no matching,
no writes, no full sync.

Each service is checked independently: one platform being logged out, or one
platform's API call failing, must never prevent the other platform's numbers
from showing. Errors are captured on the result, never raised, so a Dashboard
refresh can't crash the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass

from .clients.anilist import AniListAPIError, AniListClient
from .clients.myanimelist import MyAnimeListAPIError, MyAnimeListClient
from .config import Config
from .oauth import OAuth, create_anilist_oauth, create_myanimelist_oauth


@dataclass
class PlatformStatus:
    authenticated: bool
    anime_count: int | None = None
    manga_count: int | None = None
    error: str | None = None


@dataclass
class DashboardStats:
    anilist: PlatformStatus
    myanimelist: PlatformStatus


def _fetch_anilist_status(oauth: OAuth, config: Config) -> PlatformStatus:
    if oauth.needs_init:
        return PlatformStatus(authenticated=False)
    if not config.anilist.username:
        return PlatformStatus(authenticated=True, error="no AniList username set -- check Settings")
    try:
        client = AniListClient(
            oauth, config.anilist.username, http_timeout=config.get_http_timeout().total_seconds()
        )
        anime_count = len(client.get_user_anime_list())
        manga_count = len(client.get_user_manga_list())
    except AniListAPIError as exc:
        return PlatformStatus(authenticated=True, error=str(exc))
    return PlatformStatus(authenticated=True, anime_count=anime_count, manga_count=manga_count)


def _fetch_myanimelist_status(oauth: OAuth, config: Config) -> PlatformStatus:
    if oauth.needs_init:
        return PlatformStatus(authenticated=False)
    if not config.myanimelist.username:
        return PlatformStatus(authenticated=True, error="no MyAnimeList username set -- check Settings")
    try:
        client = MyAnimeListClient(
            oauth, config.myanimelist.username, http_timeout=config.get_http_timeout().total_seconds()
        )
        anime_count = len(client.get_user_anime_list())
        manga_count = len(client.get_user_manga_list())
    except MyAnimeListAPIError as exc:
        return PlatformStatus(authenticated=True, error=str(exc))
    return PlatformStatus(authenticated=True, anime_count=anime_count, manga_count=manga_count)


def fetch_dashboard_stats(config: Config) -> DashboardStats:
    anilist_status = _fetch_anilist_status(create_anilist_oauth(config), config)
    myanimelist_status = _fetch_myanimelist_status(create_myanimelist_oauth(config), config)
    return DashboardStats(anilist=anilist_status, myanimelist=myanimelist_status)
