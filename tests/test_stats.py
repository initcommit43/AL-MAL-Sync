"""Tests for stats.py: LibraryStats computed from AniList/MyAnimeList list
entries -- status-bucket mapping, mean score normalization across AniList's
scoreFormat variants, progress totals, and AniList's watch-time estimate
(None on MAL, since my_list_status carries no per-episode duration)."""

from __future__ import annotations

from al_mal_sync import stats
from al_mal_sync.clients.anilist import AniListListEntry, AniListMedia
from al_mal_sync.clients.myanimelist import (
    MALAnime,
    MALAnimeListStatus,
    MALManga,
    MALMangaListStatus,
    MALUserAnimeEntry,
    MALUserMangaEntry,
)


def _al_anime(
    status: str,
    *,
    score: float = 0.0,
    progress: int = 0,
    duration: int | None = None,
    genres: list[str] | None = None,
):
    return AniListListEntry(
        id=1, status=status, score=score, progress=progress,
        media=AniListMedia(id=1, duration=duration, genres=genres or []),
    )


def _al_manga(
    status: str,
    *,
    score: float = 0.0,
    progress: int = 0,
    progress_volumes: int = 0,
    genres: list[str] | None = None,
):
    return AniListListEntry(
        id=1, status=status, score=score, progress=progress, progress_volumes=progress_volumes,
        media=AniListMedia(id=1, genres=genres or []),
    )


def _mal_anime(status: str, *, score: int = 0, watched: int = 0, genres: list[str] | None = None):
    return MALUserAnimeEntry(
        anime=MALAnime(id=1, genres=genres or []),
        status=MALAnimeListStatus(status=status, score=score, num_episodes_watched=watched),
    )


def _mal_manga(
    status: str, *, score: int = 0, chapters: int = 0, volumes: int = 0, genres: list[str] | None = None
):
    return MALUserMangaEntry(
        manga=MALManga(id=1, genres=genres or []),
        status=MALMangaListStatus(
            status=status, score=score, num_chapters_read=chapters, num_volumes_read=volumes
        ),
    )


class TestComputeAnilistStats:
    def test_status_breakdown_across_all_buckets_including_repeating_folded_into_current(self) -> None:
        entries = [
            _al_anime("CURRENT"),
            _al_anime("REPEATING"),
            _al_anime("COMPLETED"),
            _al_anime("PLANNING"),
            _al_anime("PAUSED"),
            _al_anime("DROPPED"),
        ]

        result = stats.compute_anilist_stats(entries, [], "POINT_10")

        assert result.anime_status.current == 2
        assert result.anime_status.completed == 1
        assert result.anime_status.planning == 1
        assert result.anime_status.paused == 1
        assert result.anime_status.dropped == 1
        assert result.anime_status.total == 6

    def test_episodes_watched_sums_progress(self) -> None:
        entries = [_al_anime("CURRENT", progress=5), _al_anime("COMPLETED", progress=12)]

        result = stats.compute_anilist_stats(entries, [], "POINT_10")

        assert result.anime_episodes_watched == 17

    def test_manga_progress_sums_chapters_and_volumes(self) -> None:
        entries = [
            _al_manga("CURRENT", progress=10, progress_volumes=1),
            _al_manga("COMPLETED", progress=50, progress_volumes=5),
        ]

        result = stats.compute_anilist_stats([], entries, "POINT_10")

        assert result.manga_chapters_read == 60
        assert result.manga_volumes_read == 6

    def test_mean_score_ignores_unscored_entries(self) -> None:
        entries = [_al_anime("COMPLETED", score=8.0), _al_anime("COMPLETED", score=0.0)]

        result = stats.compute_anilist_stats(entries, [], "POINT_10")

        assert result.anime_mean_score == 8.0

    def test_mean_score_none_when_nothing_scored(self) -> None:
        result = stats.compute_anilist_stats([_al_anime("PLANNING")], [], "POINT_10")

        assert result.anime_mean_score is None

    def test_mean_score_normalizes_point_100_to_ten_point_scale(self) -> None:
        entries = [_al_anime("COMPLETED", score=85.0)]

        result = stats.compute_anilist_stats(entries, [], "POINT_100")

        assert result.anime_mean_score == 8.5

    def test_mean_score_normalizes_point_5_to_ten_point_scale(self) -> None:
        entries = [_al_anime("COMPLETED", score=4.0)]

        result = stats.compute_anilist_stats(entries, [], "POINT_5")

        assert result.anime_mean_score == 8.0

    def test_days_watched_estimated_from_progress_times_duration(self) -> None:
        entries = [_al_anime("COMPLETED", progress=24, duration=24)]  # 576 minutes

        result = stats.compute_anilist_stats(entries, [], "POINT_10")

        assert result.anime_days_watched == 576 / (60 * 24)

    def test_days_watched_none_when_no_entry_has_duration(self) -> None:
        entries = [_al_anime("COMPLETED", progress=24, duration=None)]

        result = stats.compute_anilist_stats(entries, [], "POINT_10")

        assert result.anime_days_watched is None

    def test_genre_counts_tally_entries_per_genre_across_anime_and_manga(self) -> None:
        anime_entries = [
            _al_anime("COMPLETED", genres=["Action", "Comedy"]),
            _al_anime("CURRENT", genres=["Action"]),
        ]
        manga_entries = [_al_manga("COMPLETED", genres=["Romance"])]

        result = stats.compute_anilist_stats(anime_entries, manga_entries, "POINT_10")

        assert result.anime_genre_counts == {"Action": 2, "Comedy": 1}
        assert result.manga_genre_counts == {"Romance": 1}

    def test_genre_counts_empty_when_entries_have_no_genres(self) -> None:
        result = stats.compute_anilist_stats([_al_anime("COMPLETED")], [], "POINT_10")

        assert result.anime_genre_counts == {}


class TestComputeMalStats:
    def test_status_breakdown_maps_mal_vocabulary_to_common_buckets(self) -> None:
        entries = [
            _mal_anime("watching"),
            _mal_anime("completed"),
            _mal_anime("plan_to_watch"),
            _mal_anime("on_hold"),
            _mal_anime("dropped"),
        ]

        result = stats.compute_mal_stats(entries, [])

        assert result.anime_status.current == 1
        assert result.anime_status.completed == 1
        assert result.anime_status.planning == 1
        assert result.anime_status.paused == 1
        assert result.anime_status.dropped == 1

    def test_episodes_watched_sums_num_episodes_watched(self) -> None:
        entries = [_mal_anime("watching", watched=5), _mal_anime("completed", watched=12)]

        result = stats.compute_mal_stats(entries, [])

        assert result.anime_episodes_watched == 17

    def test_manga_progress_sums_chapters_and_volumes(self) -> None:
        entries = [_mal_manga("reading", chapters=10, volumes=1), _mal_manga("completed", chapters=50, volumes=5)]

        result = stats.compute_mal_stats([], entries)

        assert result.manga_chapters_read == 60
        assert result.manga_volumes_read == 6

    def test_mean_score_ignores_unscored_entries(self) -> None:
        entries = [_mal_anime("completed", score=8), _mal_anime("completed", score=0)]

        result = stats.compute_mal_stats(entries, [])

        assert result.anime_mean_score == 8.0

    def test_days_watched_always_none(self) -> None:
        result = stats.compute_mal_stats([_mal_anime("watching", watched=100)], [])

        assert result.anime_days_watched is None

    def test_genre_counts_tally_entries_per_genre_across_anime_and_manga(self) -> None:
        anime_entries = [
            _mal_anime("watching", genres=["Action", "Comedy"]),
            _mal_anime("completed", genres=["Action"]),
        ]
        manga_entries = [_mal_manga("reading", genres=["Romance"])]

        result = stats.compute_mal_stats(anime_entries, manga_entries)

        assert result.anime_genre_counts == {"Action": 2, "Comedy": 1}
        assert result.manga_genre_counts == {"Romance": 1}
