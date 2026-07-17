"""Tests for the MediaService adapters: get_by_id/get_by_name/get_by_mal_id
not-found handling, bare-target construction, and the outgoing update() calls
(score conversion, and the "finish date only sent once completed" rule)."""

from __future__ import annotations

from datetime import date
from typing import Any

from al_mal_sync.clients.anilist import AniListMedia, AniListNotFoundError, AniListTitle
from al_mal_sync.clients.myanimelist import MALAnime, MALManga, MALTitles, MyAnimeListNotFoundError
from al_mal_sync.models import Anime, AnimeStatus, Manga, MangaStatus
from al_mal_sync.sync.service import (
    AniListAnimeService,
    AniListMangaService,
    MyAnimeListAnimeService,
    MyAnimeListMangaService,
)


class _FakeAniListClient:
    def __init__(
        self,
        *,
        anime_by_id: dict[int, AniListMedia] | None = None,
        anime_by_mal_id: dict[int, AniListMedia] | None = None,
        animes_by_name: dict[str, list[AniListMedia]] | None = None,
        manga_by_id: dict[int, AniListMedia] | None = None,
        manga_by_mal_id: dict[int, AniListMedia] | None = None,
        mangas_by_name: dict[str, list[AniListMedia]] | None = None,
    ) -> None:
        self._anime_by_id = anime_by_id or {}
        self._anime_by_mal_id = anime_by_mal_id or {}
        self._animes_by_name = animes_by_name or {}
        self._manga_by_id = manga_by_id or {}
        self._manga_by_mal_id = manga_by_mal_id or {}
        self._mangas_by_name = mangas_by_name or {}
        self.update_anime_calls: list[tuple[Any, ...]] = []
        self.update_manga_calls: list[tuple[Any, ...]] = []

    def get_anime_by_id(self, media_id: int) -> AniListMedia:
        if media_id not in self._anime_by_id:
            raise AniListNotFoundError(f"no anime {media_id}")
        return self._anime_by_id[media_id]

    def get_anime_by_mal_id(self, mal_id: int) -> AniListMedia:
        if mal_id not in self._anime_by_mal_id:
            raise AniListNotFoundError(f"no anime with mal id {mal_id}")
        return self._anime_by_mal_id[mal_id]

    def get_animes_by_name(self, name: str) -> list[AniListMedia]:
        return self._animes_by_name.get(name, [])

    def get_manga_by_id(self, media_id: int) -> AniListMedia:
        if media_id not in self._manga_by_id:
            raise AniListNotFoundError(f"no manga {media_id}")
        return self._manga_by_id[media_id]

    def get_manga_by_mal_id(self, mal_id: int) -> AniListMedia:
        if mal_id not in self._manga_by_mal_id:
            raise AniListNotFoundError(f"no manga with mal id {mal_id}")
        return self._manga_by_mal_id[mal_id]

    def get_mangas_by_name(self, name: str) -> list[AniListMedia]:
        return self._mangas_by_name.get(name, [])

    def update_anime_entry(self, media_id, status, progress, score, *, started_at=None, completed_at=None):
        self.update_anime_calls.append((media_id, status, progress, score, started_at, completed_at))

    def update_manga_entry(
        self, media_id, status, progress, progress_volumes, score, *, started_at=None, completed_at=None
    ):
        self.update_manga_calls.append(
            (media_id, status, progress, progress_volumes, score, started_at, completed_at)
        )


class _FakeMyAnimeListClient:
    def __init__(
        self,
        *,
        anime_by_id: dict[int, MALAnime] | None = None,
        animes_by_name: dict[str, list[MALAnime]] | None = None,
        manga_by_id: dict[int, MALManga] | None = None,
        mangas_by_name: dict[str, list[MALManga]] | None = None,
    ) -> None:
        self._anime_by_id = anime_by_id or {}
        self._animes_by_name = animes_by_name or {}
        self._manga_by_id = manga_by_id or {}
        self._mangas_by_name = mangas_by_name or {}
        self.update_anime_calls: list[tuple[Any, ...]] = []
        self.update_manga_calls: list[tuple[Any, ...]] = []

    def get_anime_by_id(self, anime_id: int) -> MALAnime:
        if anime_id not in self._anime_by_id:
            raise MyAnimeListNotFoundError(f"no anime {anime_id}")
        return self._anime_by_id[anime_id]

    def get_animes_by_name(self, name: str) -> list[MALAnime]:
        return self._animes_by_name.get(name, [])

    def get_manga_by_id(self, manga_id: int) -> MALManga:
        if manga_id not in self._manga_by_id:
            raise MyAnimeListNotFoundError(f"no manga {manga_id}")
        return self._manga_by_id[manga_id]

    def get_mangas_by_name(self, name: str) -> list[MALManga]:
        return self._mangas_by_name.get(name, [])

    def update_anime(self, anime_id, **fields):
        self.update_anime_calls.append((anime_id, fields))

    def update_manga(self, manga_id, **fields):
        self.update_manga_calls.append((manga_id, fields))


class TestAniListAnimeService:
    def test_get_by_id_wraps_media(self) -> None:
        media = AniListMedia(id=100, title=AniListTitle(english="Show"))
        client = _FakeAniListClient(anime_by_id={100: media})
        service = AniListAnimeService(client, "POINT_10", reverse=True)
        result = service.get_by_id(100)
        assert isinstance(result, Anime)
        assert result.id_anilist == 100
        assert result.is_reverse is True

    def test_get_by_id_not_found_returns_none(self) -> None:
        service = AniListAnimeService(_FakeAniListClient(), "POINT_10", reverse=True)
        assert service.get_by_id(1) is None

    def test_get_by_mal_id_not_found_returns_none(self) -> None:
        service = AniListAnimeService(_FakeAniListClient(), "POINT_10", reverse=True)
        assert service.get_by_mal_id(1) is None

    def test_get_by_name_wraps_all_results(self) -> None:
        media = AniListMedia(id=1, title=AniListTitle(english="A"))
        client = _FakeAniListClient(animes_by_name={"A": [media]})
        service = AniListAnimeService(client, "POINT_10", reverse=False)
        results = service.get_by_name("A")
        assert len(results) == 1
        assert results[0].id_anilist == 1

    def test_update_denormalizes_score_and_sends_completed_date(self) -> None:
        client = _FakeAniListClient()
        service = AniListAnimeService(client, "POINT_10", reverse=True)
        source = Anime(
            status=AnimeStatus.COMPLETED,
            score=8,
            progress=12,
            started_at=date(2020, 1, 1),
            finished_at=date(2020, 3, 1),
        )
        service.update(source, 42)
        assert client.update_anime_calls == [
            (42, "COMPLETED", 12, 8.0, date(2020, 1, 1), date(2020, 3, 1))
        ]

    def test_update_omits_finish_date_when_not_completed(self) -> None:
        client = _FakeAniListClient()
        service = AniListAnimeService(client, "POINT_10", reverse=True)
        source = Anime(status=AnimeStatus.WATCHING, score=0, progress=3, finished_at=date(2020, 3, 1))
        service.update(source, 42)
        media_id, status, progress, score, started_at, completed_at = client.update_anime_calls[0]
        assert completed_at is None

    def test_update_sends_repeating_status_while_rewatching(self) -> None:
        client = _FakeAniListClient()
        service = AniListAnimeService(client, "POINT_10", reverse=True)
        source = Anime(status=AnimeStatus.WATCHING, score=0, progress=3, is_rewatching=True)
        service.update(source, 42)
        _, status, *_ = client.update_anime_calls[0]
        assert status == "REPEATING"


class TestAniListMangaService:
    def test_update_includes_progress_volumes(self) -> None:
        client = _FakeAniListClient()
        service = AniListMangaService(client, "POINT_10", reverse=True)
        source = Manga(status=MangaStatus.READING, score=5, progress=10, progress_volumes=2)
        service.update(source, 7)
        assert client.update_manga_calls == [(7, "CURRENT", 10, 2, 5.0, None, None)]

    def test_update_sends_repeating_status_while_rereading(self) -> None:
        client = _FakeAniListClient()
        service = AniListMangaService(client, "POINT_10", reverse=True)
        source = Manga(status=MangaStatus.READING, score=0, progress=10, is_rereading=True)
        service.update(source, 7)
        _, status, *_ = client.update_manga_calls[0]
        assert status == "REPEATING"


class TestMyAnimeListAnimeService:
    def test_get_by_id_wraps_bare_media(self) -> None:
        anime = MALAnime(id=5, title="Show", alternative_titles=MALTitles(en="Show EN"))
        client = _FakeMyAnimeListClient(anime_by_id={5: anime})
        service = MyAnimeListAnimeService(client)
        result = service.get_by_id(5)
        assert isinstance(result, Anime)
        assert result.id_mal == 5
        assert result.title_en == "Show EN"

    def test_get_by_id_not_found_returns_none(self) -> None:
        service = MyAnimeListAnimeService(_FakeMyAnimeListClient())
        assert service.get_by_id(5) is None

    def test_update_uses_status_value_and_raw_score(self) -> None:
        client = _FakeMyAnimeListClient()
        service = MyAnimeListAnimeService(client)
        source = Anime(status=AnimeStatus.COMPLETED, score=7, progress=24, finished_at=date(2021, 5, 1))
        service.update(source, 5)
        anime_id, fields = client.update_anime_calls[0]
        assert anime_id == 5
        assert fields["status"] == "completed"
        assert fields["score"] == 7
        assert fields["num_watched_episodes"] == 24
        assert fields["finish_date"] == date(2021, 5, 1)

    def test_update_omits_finish_date_when_not_completed(self) -> None:
        client = _FakeMyAnimeListClient()
        service = MyAnimeListAnimeService(client)
        source = Anime(status=AnimeStatus.WATCHING, score=0, progress=3, finished_at=date(2021, 5, 1))
        service.update(source, 5)
        _, fields = client.update_anime_calls[0]
        assert fields["finish_date"] is None

    def test_update_passes_is_rewatching_through(self) -> None:
        client = _FakeMyAnimeListClient()
        service = MyAnimeListAnimeService(client)
        source = Anime(status=AnimeStatus.WATCHING, score=0, progress=3, is_rewatching=True)
        service.update(source, 5)
        _, fields = client.update_anime_calls[0]
        assert fields["is_rewatching"] is True


class TestMyAnimeListMangaService:
    def test_update_includes_volumes(self) -> None:
        client = _FakeMyAnimeListClient()
        service = MyAnimeListMangaService(client)
        source = Manga(status=MangaStatus.READING, score=6, progress=10, progress_volumes=1)
        service.update(source, 3)
        manga_id, fields = client.update_manga_calls[0]
        assert manga_id == 3
        assert fields["num_chapters_read"] == 10
        assert fields["num_volumes_read"] == 1

    def test_update_passes_is_rereading_through(self) -> None:
        client = _FakeMyAnimeListClient()
        service = MyAnimeListMangaService(client)
        source = Manga(status=MangaStatus.READING, score=0, progress=10, is_rereading=True)
        service.update(source, 3)
        _, fields = client.update_manga_calls[0]
        assert fields["is_rereading"] is True
