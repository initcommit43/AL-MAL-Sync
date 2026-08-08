"""Live per-platform list snapshot for the GUI Dashboard: library-size counts
plus the richer LibraryStats (status breakdown, mean score, progress, AniList's
watch-time estimate) computed from the same get_user_anime_list()/
get_user_manga_list() call -- no matching, no writes, no full sync, and no
second network round-trip just to get the stats widgets their data.

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
from .stats import LibraryStats, compute_anilist_stats, compute_mal_stats


@dataclass
class PlatformStatus:
    authenticated: bool
    anime_count: int | None = None
    manga_count: int | None = None
    error: str | None = None
    stats: LibraryStats | None = None


@dataclass
class DashboardStats:
    anilist: PlatformStatus
    myanimelist: PlatformStatus


def _fetch_anilist_status(oauth: OAuth, config: Config) -> PlatformStatus:
    if oauth.needs_init:
        return PlatformStatus(authenticated=False)
    if not config.anilist.username:
        return PlatformStatus(
            authenticated=True,
            error="no AniList username set -- use \"Fetch my username\" on the Login page, or set it in Settings",
        )
    try:
        client = AniListClient(
            oauth, config.anilist.username, http_timeout=config.get_http_timeout().total_seconds()
        )
        anime_entries = client.get_user_anime_list()
        manga_entries = client.get_user_manga_list()
    except AniListAPIError as exc:
        return PlatformStatus(authenticated=True, error=str(exc))
    try:
        # Only needed to interpret entry.score's scale for the stats widgets
        # -- a failure here shouldn't blank out the counts we already have.
        score_format = client.get_user_score_format()
    except AniListAPIError:
        score_format = "POINT_10"
    stats = compute_anilist_stats(anime_entries, manga_entries, score_format)
    return PlatformStatus(
        authenticated=True, anime_count=len(anime_entries), manga_count=len(manga_entries), stats=stats
    )


def _fetch_myanimelist_status(oauth: OAuth, config: Config) -> PlatformStatus:
    if oauth.needs_init:
        return PlatformStatus(authenticated=False)
    if not config.myanimelist.username:
        return PlatformStatus(
            authenticated=True,
            error=(
                "no MyAnimeList username set -- use \"Fetch my username\" on the Login page, "
                "or set it in Settings"
            ),
        )
    try:
        client = MyAnimeListClient(
            oauth, config.myanimelist.username, http_timeout=config.get_http_timeout().total_seconds()
        )
        anime_entries = client.get_user_anime_list()
        manga_entries = client.get_user_manga_list()
    except MyAnimeListAPIError as exc:
        return PlatformStatus(authenticated=True, error=str(exc))
    stats = compute_mal_stats(anime_entries, manga_entries)
    return PlatformStatus(
        authenticated=True, anime_count=len(anime_entries), manga_count=len(manga_entries), stats=stats
    )


def fetch_dashboard_stats(config: Config) -> DashboardStats:
    anilist_status = _fetch_anilist_status(create_anilist_oauth(config), config)
    myanimelist_status = _fetch_myanimelist_status(create_myanimelist_oauth(config), config)
    return DashboardStats(anilist=anilist_status, myanimelist=myanimelist_status)
