"""Cross-platform library statistics, computed from the same list entries
dashboard.py already fetches for the Dashboard's library-size cards -- no
extra API calls, since both platforms rate-limit aggressively (see
dashboard_tab.py's staleness-guard comment).

AniList's per-entry data is richer than MAL's: a normalizable score scale
(via scoreFormat) and per-episode duration for a watch-time estimate. MAL's
`my_list_status` has neither. LibraryStats carries the AniList-only fields as
optional, None when the source can't supply them -- it's the GUI's job to
hide the widgets that need them when MyAnimeList is the selected stats
source.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .clients.anilist import AniListListEntry
from .clients.myanimelist import MALUserAnimeEntry, MALUserMangaEntry

# Canonical buckets both platforms' list statuses map onto. AniList's
# REPEATING (rewatching/rereading) folds into "current", matching MAL's own
# modeling of a rewatch as status="watching" + is_rewatching=True rather than
# a distinct status value.
_STATUS_KEYS = ("current", "completed", "planning", "paused", "dropped")

_ANILIST_STATUS_MAP = {
    "CURRENT": "current",
    "REPEATING": "current",
    "PLANNING": "planning",
    "COMPLETED": "completed",
    "DROPPED": "dropped",
    "PAUSED": "paused",
}

_MAL_STATUS_MAP = {
    "watching": "current",
    "reading": "current",
    "plan_to_watch": "planning",
    "plan_to_read": "planning",
    "completed": "completed",
    "dropped": "dropped",
    "on_hold": "paused",
}

# AniList score meaning depends on the user's chosen scoreFormat (their
# profile display setting); this is the "how many points is a perfect score"
# side of the same scale sync/score.py normalizes for writes. Values not in
# this map (shouldn't happen -- AniList only defines these five) fall back to
# POINT_10's scale.
_ANILIST_SCORE_SCALE = {
    "POINT_100": 100.0,
    "POINT_10": 10.0,
    "POINT_10_DECIMAL": 10.0,
    "POINT_5": 5.0,
    "POINT_3": 3.0,
}


@dataclass
class StatusCounts:
    current: int = 0
    completed: int = 0
    planning: int = 0
    paused: int = 0
    dropped: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, key) for key in _STATUS_KEYS)


def _bump(counts: StatusCounts, bucket: str | None) -> None:
    if bucket is not None:
        setattr(counts, bucket, getattr(counts, bucket) + 1)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _bump_genres(counts: dict[str, int], genres: list[str]) -> None:
    for genre in genres:
        counts[genre] = counts.get(genre, 0) + 1


def _bump_score_bucket(counts: dict[int, int], normalized_score: float) -> None:
    """Buckets a 0-10-scale score into a whole 1-10 histogram bin -- the
    GUI's ScoreDistributionCard draws one fixed 1-10 column per bucket, the
    same shape as MAL's own native score scale, so both platforms' data
    ends up on identical axes regardless of AniList's scoreFormat."""
    bucket = max(1, min(10, round(normalized_score)))
    counts[bucket] = counts.get(bucket, 0) + 1


@dataclass
class LibraryStats:
    anime_status: StatusCounts = field(default_factory=StatusCounts)
    manga_status: StatusCounts = field(default_factory=StatusCounts)
    anime_episodes_watched: int = 0
    manga_chapters_read: int = 0
    manga_volumes_read: int = 0
    anime_mean_score: float | None = None  # normalized to a 0-10 scale
    manga_mean_score: float | None = None  # normalized to a 0-10 scale
    # None when the source can't supply per-episode duration (always None for
    # MAL -- my_list_status has no duration field to estimate watch time from).
    anime_days_watched: float | None = None
    # Raw entry-count-per-genre, unsorted -- the GUI's GenreBreakdownCard
    # ranks and truncates to a top-N list for display.
    anime_genre_counts: dict[str, int] = field(default_factory=dict)
    manga_genre_counts: dict[str, int] = field(default_factory=dict)
    # Entry counts per whole-number score bucket (1-10), scored entries only.
    anime_score_distribution: dict[int, int] = field(default_factory=dict)
    manga_score_distribution: dict[int, int] = field(default_factory=dict)


def compute_anilist_stats(
    anime_entries: list[AniListListEntry],
    manga_entries: list[AniListListEntry],
    score_format: str,
) -> LibraryStats:
    stats = LibraryStats()
    scale = _ANILIST_SCORE_SCALE.get(score_format, 10.0)

    anime_scores: list[float] = []
    duration_minutes = 0.0
    for entry in anime_entries:
        _bump(stats.anime_status, _ANILIST_STATUS_MAP.get(entry.status))
        stats.anime_episodes_watched += entry.progress
        if entry.score:
            normalized = entry.score / scale * 10.0
            anime_scores.append(normalized)
            _bump_score_bucket(stats.anime_score_distribution, normalized)
        if entry.media.duration:
            duration_minutes += entry.progress * entry.media.duration
        _bump_genres(stats.anime_genre_counts, entry.media.genres)

    manga_scores: list[float] = []
    for entry in manga_entries:
        _bump(stats.manga_status, _ANILIST_STATUS_MAP.get(entry.status))
        stats.manga_chapters_read += entry.progress
        stats.manga_volumes_read += entry.progress_volumes
        if entry.score:
            normalized = entry.score / scale * 10.0
            manga_scores.append(normalized)
            _bump_score_bucket(stats.manga_score_distribution, normalized)
        _bump_genres(stats.manga_genre_counts, entry.media.genres)

    stats.anime_mean_score = _mean(anime_scores)
    stats.manga_mean_score = _mean(manga_scores)
    stats.anime_days_watched = duration_minutes / (60 * 24) if duration_minutes else None
    return stats


def compute_mal_stats(
    anime_entries: list[MALUserAnimeEntry],
    manga_entries: list[MALUserMangaEntry],
) -> LibraryStats:
    stats = LibraryStats()

    anime_scores: list[float] = []
    for entry in anime_entries:
        _bump(stats.anime_status, _MAL_STATUS_MAP.get(entry.status.status))
        stats.anime_episodes_watched += entry.status.num_episodes_watched
        if entry.status.score:
            anime_scores.append(float(entry.status.score))
            _bump_score_bucket(stats.anime_score_distribution, entry.status.score)
        _bump_genres(stats.anime_genre_counts, entry.anime.genres)

    manga_scores: list[float] = []
    for entry in manga_entries:
        _bump(stats.manga_status, _MAL_STATUS_MAP.get(entry.status.status))
        stats.manga_chapters_read += entry.status.num_chapters_read
        stats.manga_volumes_read += entry.status.num_volumes_read
        if entry.status.score:
            manga_scores.append(float(entry.status.score))
            _bump_score_bucket(stats.manga_score_distribution, entry.status.score)
        _bump_genres(stats.manga_genre_counts, entry.manga.genres)

    stats.anime_mean_score = _mean(anime_scores)
    stats.manga_mean_score = _mean(manga_scores)
    return stats
