"""MediaService adapters: wrap the AniList/MyAnimeList clients behind the
uniform interface strategies.py's MediaService/MediaServiceWithMalId protocols
expect, and apply outgoing updates.

Ported from the reference Go tool's service.go. Four concrete classes (one per
service x media type) rather than a single generic class: AniList and MAL each
already expose separate anime/manga methods on their clients (see
clients/anilist.py, clients/myanimelist.py), so this just mirrors that split
instead of re-introducing type branching here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Anime, AnimeStatus, Manga, MangaStatus
from .score import denormalize_score_for_anilist

if TYPE_CHECKING:
    from ..clients.anilist import AniListClient
    from ..clients.myanimelist import MyAnimeListClient

from ..clients.anilist import AniListNotFoundError
from ..clients.myanimelist import MyAnimeListNotFoundError


class AniListAnimeService:
    """AniList-side MediaService for anime. Used as the target service when
    syncing MAL -> AniList (``reverse=True``)."""

    def __init__(self, client: AniListClient, score_format: str, *, reverse: bool) -> None:
        self.client = client
        self.score_format = score_format
        self.reverse = reverse

    def get_by_id(self, target_id: int) -> Anime | None:
        try:
            media = self.client.get_anime_by_id(target_id)
        except AniListNotFoundError:
            return None
        return Anime.from_anilist_media(media, reverse=self.reverse)

    def get_by_name(self, name: str) -> list[Anime]:
        return [
            Anime.from_anilist_media(media, reverse=self.reverse)
            for media in self.client.get_animes_by_name(name)
        ]

    def get_by_mal_id(self, mal_id: int) -> Anime | None:
        try:
            media = self.client.get_anime_by_mal_id(mal_id)
        except AniListNotFoundError:
            return None
        return Anime.from_anilist_media(media, reverse=self.reverse)

    def update(self, source: Anime, target_id: int) -> None:
        self.client.update_anime_entry(
            target_id,
            source.get_anilist_status_string(),
            source.progress,
            denormalize_score_for_anilist(source.score, self.score_format),
            started_at=source.started_at,
            # Only push a finish date once the entry is actually completed; see
            # docs/date-sync.md.
            completed_at=source.finished_at if source.status == AnimeStatus.COMPLETED else None,
        )


class AniListMangaService:
    """AniList-side MediaService for manga. Used as the target service when
    syncing MAL -> AniList (``reverse=True``)."""

    def __init__(self, client: AniListClient, score_format: str, *, reverse: bool) -> None:
        self.client = client
        self.score_format = score_format
        self.reverse = reverse

    def get_by_id(self, target_id: int) -> Manga | None:
        try:
            media = self.client.get_manga_by_id(target_id)
        except AniListNotFoundError:
            return None
        return Manga.from_anilist_media(media, reverse=self.reverse)

    def get_by_name(self, name: str) -> list[Manga]:
        return [
            Manga.from_anilist_media(media, reverse=self.reverse)
            for media in self.client.get_mangas_by_name(name)
        ]

    def get_by_mal_id(self, mal_id: int) -> Manga | None:
        try:
            media = self.client.get_manga_by_mal_id(mal_id)
        except AniListNotFoundError:
            return None
        return Manga.from_anilist_media(media, reverse=self.reverse)

    def update(self, source: Manga, target_id: int) -> None:
        self.client.update_manga_entry(
            target_id,
            source.get_anilist_status_string(),
            source.progress,
            source.progress_volumes,
            denormalize_score_for_anilist(source.score, self.score_format),
            started_at=source.started_at,
            completed_at=source.finished_at if source.status == MangaStatus.COMPLETED else None,
        )


class MyAnimeListAnimeService:
    """MAL-side MediaService for anime. Used as the target service when
    syncing AniList -> MAL (``reverse=False``). MAL has no "look up by AniList
    ID" endpoint, so unlike the AniList services this has no get_by_mal_id."""

    def __init__(self, client: MyAnimeListClient, *, reverse: bool = False) -> None:
        self.client = client
        self.reverse = reverse

    def get_by_id(self, target_id: int) -> Anime | None:
        try:
            anime = self.client.get_anime_by_id(target_id)
        except MyAnimeListNotFoundError:
            return None
        return Anime.from_mal_media(anime, reverse=self.reverse)

    def get_by_name(self, name: str) -> list[Anime]:
        return [
            Anime.from_mal_media(anime, reverse=self.reverse)
            for anime in self.client.get_animes_by_name(name)
        ]

    def update(self, source: Anime, target_id: int) -> None:
        self.client.update_anime(
            target_id,
            status=source.status.value,
            score=source.score,
            num_watched_episodes=source.progress,
            start_date=source.started_at,
            finish_date=source.finished_at if source.status == AnimeStatus.COMPLETED else None,
            is_rewatching=source.is_rewatching,
        )


class MyAnimeListMangaService:
    """MAL-side MediaService for manga. Used as the target service when
    syncing AniList -> MAL (``reverse=False``)."""

    def __init__(self, client: MyAnimeListClient, *, reverse: bool = False) -> None:
        self.client = client
        self.reverse = reverse

    def get_by_id(self, target_id: int) -> Manga | None:
        try:
            manga = self.client.get_manga_by_id(target_id)
        except MyAnimeListNotFoundError:
            return None
        return Manga.from_mal_media(manga, reverse=self.reverse)

    def get_by_name(self, name: str) -> list[Manga]:
        return [
            Manga.from_mal_media(manga, reverse=self.reverse)
            for manga in self.client.get_mangas_by_name(name)
        ]

    def update(self, source: Manga, target_id: int) -> None:
        self.client.update_manga(
            target_id,
            status=source.status.value,
            score=source.score,
            num_chapters_read=source.progress,
            num_volumes_read=source.progress_volumes,
            start_date=source.started_at,
            finish_date=source.finished_at if source.status == MangaStatus.COMPLETED else None,
            is_rereading=source.is_rereading,
        )
