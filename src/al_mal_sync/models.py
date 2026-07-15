"""Shared data models: list entries, media, sync direction, etc.

Ported from the reference Go tool's anime.go/manga.go/utils.go. `Anime` and
`Manga` implement the Source/Target protocols below and carry the matching
logic (title similarity, "is this actually the same entry" checks) that the
Phase 5 strategy chain calls.

Deliberate deviation: Go threads a context.Context through SameTypeWithTarget/
SameTitleWithTarget purely for debug logging. Python's logging doesn't need a
per-call context object for that, so it's dropped from these signatures here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from .sync.dates import parse_mal_date, same_dates
from .sync.score import normalize_score_for_mal

if TYPE_CHECKING:
    from .clients.anilist import AniListListEntry, AniListMedia
    from .clients.myanimelist import MALUserAnimeEntry, MALUserMangaEntry


# --------------------------------------------------------------------------
# Source / Target protocols
# --------------------------------------------------------------------------


class Target(Protocol):
    def get_target_id(self) -> int: ...
    def get_title(self) -> str: ...
    def __str__(self) -> str: ...


class Source(Protocol):
    def get_status_string(self) -> str: ...
    def get_target_id(self) -> int: ...
    def get_source_id(self) -> int: ...
    def get_title(self) -> str: ...
    def get_string_diff_with_target(self, target: Target) -> str: ...
    def same_progress_with_target(self, target: Target) -> bool: ...
    def same_type_with_target(self, target: Target) -> bool: ...
    def same_title_with_target(self, target: Target) -> bool: ...
    def __str__(self) -> str: ...


# --------------------------------------------------------------------------
# Status enums
# --------------------------------------------------------------------------


class AnimeStatus(str, Enum):
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLAN_TO_WATCH = "plan_to_watch"
    UNKNOWN = "unknown"

    def to_anilist_status(self) -> str:
        return _ANIME_STATUS_TO_ANILIST.get(self, "")


class MangaStatus(str, Enum):
    READING = "reading"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLAN_TO_READ = "plan_to_read"
    UNKNOWN = "unknown"

    def to_anilist_status(self) -> str:
        return _MANGA_STATUS_TO_ANILIST.get(self, "")


_ANIME_STATUS_TO_ANILIST = {
    AnimeStatus.WATCHING: "CURRENT",
    AnimeStatus.COMPLETED: "COMPLETED",
    AnimeStatus.ON_HOLD: "PAUSED",
    AnimeStatus.DROPPED: "DROPPED",
    AnimeStatus.PLAN_TO_WATCH: "PLANNING",
}

_ANILIST_TO_ANIME_STATUS = {
    "CURRENT": AnimeStatus.WATCHING,
    "COMPLETED": AnimeStatus.COMPLETED,
    "PAUSED": AnimeStatus.ON_HOLD,
    "DROPPED": AnimeStatus.DROPPED,
    "PLANNING": AnimeStatus.PLAN_TO_WATCH,
    "REPEATING": AnimeStatus.WATCHING,  # TODO: handle rewatching distinctly
}

_MANGA_STATUS_TO_ANILIST = {
    MangaStatus.READING: "CURRENT",
    MangaStatus.COMPLETED: "COMPLETED",
    MangaStatus.ON_HOLD: "PAUSED",
    MangaStatus.DROPPED: "DROPPED",
    MangaStatus.PLAN_TO_READ: "PLANNING",
}

_ANILIST_TO_MANGA_STATUS = {
    "CURRENT": MangaStatus.READING,
    "COMPLETED": MangaStatus.COMPLETED,
    "PAUSED": MangaStatus.ON_HOLD,
    "DROPPED": MangaStatus.DROPPED,
    "PLANNING": MangaStatus.PLAN_TO_READ,
    "REPEATING": MangaStatus.READING,
}


def _anilist_status_to_anime_status(status: str) -> AnimeStatus:
    return _ANILIST_TO_ANIME_STATUS.get(status, AnimeStatus.UNKNOWN)


def _anilist_status_to_manga_status(status: str) -> MangaStatus:
    return _ANILIST_TO_MANGA_STATUS.get(status, MangaStatus.UNKNOWN)


def _mal_status_to_anime_status(status: str) -> AnimeStatus:
    try:
        return AnimeStatus(status)
    except ValueError:
        return AnimeStatus.UNKNOWN


def _mal_status_to_manga_status(status: str) -> MangaStatus:
    try:
        return MangaStatus(status)
    except ValueError:
        return MangaStatus.UNKNOWN


# --------------------------------------------------------------------------
# Title matching (used by same_title_with_target / strategies.py's TitleStrategy)
# --------------------------------------------------------------------------

_BRACKETS_RE = re.compile(r"\(.*\)")
_WHITESPACE_RE = re.compile(r"\s+")
_SIMILARITY_THRESHOLD = 98.0
_LEVENSHTEIN_THRESHOLD = 98.0


def _normalize_title(title: str) -> str:
    normalized = title.lower()
    normalized = _BRACKETS_RE.sub("", normalized)
    for old, new in ((":", ""), ("!", ""), ("?", ""), (".", ""), ("-", " "), ("_", " ")):
        normalized = normalized.replace(old, new)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def _levenshtein_distance(s1: str, s2: str) -> int:
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1, start=1):
        current_row = [i] + [0] * len(s2)
        for j, c2 in enumerate(s2, start=1):
            cost = 0 if c1 == c2 else 1
            current_row[j] = min(
                previous_row[j] + 1,
                current_row[j - 1] + 1,
                previous_row[j - 1] + cost,
            )
        previous_row = current_row
    return previous_row[-1]


def _title_similarity(title1: str, title2: str) -> float:
    normalized1 = _normalize_title(title1)
    normalized2 = _normalize_title(title2)
    if normalized1 == normalized2:
        return 100.0

    words1 = normalized1.split()
    words2 = normalized2.split()
    if not words1 or not words2:
        return 0.0

    common = sum(1 for word in words1 if word in words2)
    total = len(words1) + len(words2)
    return (common * 2 / total) * 100.0


def _title_levenshtein_similarity(title1: str, title2: str) -> float:
    normalized1 = _normalize_title(title1)
    normalized2 = _normalize_title(title2)
    if normalized1 == normalized2:
        return 100.0

    max_len = max(len(normalized1), len(normalized2))
    if max_len == 0:
        return 100.0

    distance = _levenshtein_distance(normalized1, normalized2)
    return max((1.0 - distance / max_len) * 100.0, 0.0)


def _exact_match(t1: str, t2: str) -> bool:
    return bool(t1) and bool(t2) and t1.casefold() == t2.casefold()


def _normalized_match(t1: str, t2: str) -> bool:
    return bool(t1) and bool(t2) and _normalize_title(t1) == _normalize_title(t2)


def _fuzzy_match(t1: str, t2: str, threshold: float) -> bool:
    return bool(t1) and bool(t2) and _title_similarity(t1, t2) >= threshold


def _levenshtein_match(t1: str, t2: str, threshold: float) -> bool:
    return bool(t1) and bool(t2) and _title_levenshtein_similarity(t1, t2) >= threshold


def _title_matching_levels(
    title_en1: str, title_jp1: str, title_romaji1: str,
    title_en2: str, title_jp2: str, title_romaji2: str,
) -> bool:
    """Multi-level title match: exact -> normalized -> fuzzy -> Levenshtein,
    tried across English/Japanese/Romaji titles at each level before moving on."""
    if _exact_match(title_en1, title_en2):
        return True
    if _exact_match(title_jp1, title_jp2):
        return True
    if _exact_match(title_romaji1, title_romaji2):
        return True

    if _normalized_match(title_en1, title_en2):
        return True
    if _normalized_match(title_jp1, title_jp2):
        return True
    if _normalized_match(title_romaji1, title_romaji2):
        return True

    if _fuzzy_match(title_en1, title_en2, _SIMILARITY_THRESHOLD):
        return True
    if _fuzzy_match(title_jp1, title_jp2, _SIMILARITY_THRESHOLD):
        return True
    if _fuzzy_match(title_romaji1, title_romaji2, _SIMILARITY_THRESHOLD):
        return True

    if _levenshtein_match(title_en1, title_en2, _LEVENSHTEIN_THRESHOLD):
        return True
    if _levenshtein_match(title_jp1, title_jp2, _LEVENSHTEIN_THRESHOLD):
        return True
    return bool(_levenshtein_match(title_romaji1, title_romaji2, _LEVENSHTEIN_THRESHOLD))


def _build_diff_string(*pairs: tuple[str, object, object]) -> str:
    parts = [f"{name}: {a!r} -> {b!r}" for name, a, b in pairs if a != b]
    return "Diff{" + ", ".join(parts) + "}"


# --------------------------------------------------------------------------
# Anime
# --------------------------------------------------------------------------


@dataclass
class Anime:
    id_anilist: int = 0
    id_mal: int = 0
    title_en: str = ""
    title_jp: str = ""
    title_romaji: str = ""
    status: AnimeStatus = AnimeStatus.UNKNOWN
    score: int = 0
    progress: int = 0
    num_episodes: int = 0
    season_year: int = 0
    started_at: date | None = None
    finished_at: date | None = None
    is_favourite: bool = False
    # Internal: True when this entry is used in the MAL->AniList reverse sync
    # direction. Changes which ID field get_target_id()/get_source_id() use.
    is_reverse: bool = False

    def get_target_id(self) -> int:
        return self.id_anilist if self.is_reverse else self.id_mal

    def get_source_id(self) -> int:
        return self.id_mal if self.is_reverse else self.id_anilist

    def get_status_string(self) -> str:
        return self.status.value

    def get_title(self) -> str:
        return self.title_en or self.title_jp or self.title_romaji

    def get_string_diff_with_target(self, target: Target) -> str:
        if not isinstance(target, Anime):
            return "Diff{undefined}"
        return _build_diff_string(
            ("Status", self.status, target.status),
            ("Score", self.score, target.score),
            ("Progress", self.progress, target.progress),
            ("NumEpisodes", self.num_episodes, target.num_episodes),
            ("StartedAt", self.started_at, target.started_at),
            ("FinishedAt", self.finished_at, target.finished_at),
            ("TitleEN", self.title_en, target.title_en),
            ("TitleJP", self.title_jp, target.title_jp),
            ("TitleRomaji", self.title_romaji, target.title_romaji),
        )

    def same_progress_with_target(self, target: Target) -> bool:
        if not isinstance(target, Anime):
            return False
        if self.status != target.status:
            return False
        if self.score != target.score:
            return False
        if not same_dates(self.started_at, target.started_at):
            return False
        # Only compare the finish date once completed: a not-yet-finished entry
        # can carry a stale finish date on one service that the other ignores,
        # comparing it anyway would trigger updates that never settle.
        if self.status == AnimeStatus.COMPLETED and not same_dates(
            self.finished_at, target.finished_at
        ):
            return False

        progress_matches = self.progress == target.progress
        if self.num_episodes == target.num_episodes:
            return progress_matches
        if self.num_episodes == 0 or target.num_episodes == 0:
            return progress_matches
        if progress_matches:
            return True

        return (self.num_episodes - self.progress) == (target.num_episodes - target.progress)

    def same_type_with_target(self, target: Target) -> bool:
        if not isinstance(target, Anime):
            return False
        if self.id_mal > 0 and target.id_mal > 0 and self.id_mal == target.id_mal:
            return True
        if self.id_anilist > 0 and target.id_anilist > 0 and self.id_anilist == target.id_anilist:
            return True
        return self.same_title_with_target(target)

    def same_title_with_target(self, target: Target) -> bool:
        if not isinstance(target, Anime):
            return False
        if not _title_matching_levels(
            self.title_en, self.title_jp, self.title_romaji,
            target.title_en, target.title_jp, target.title_romaji,
        ):
            return False

        # Reject if episode counts are wildly different even when titles match
        # (catches e.g. a 1-episode special matching a 24-episode TV series).
        if self.num_episodes > 0 and target.num_episodes > 0:
            min_eps, max_eps = sorted((self.num_episodes, target.num_episodes))
            percent_diff = (max_eps - min_eps) / max_eps * 100
            if percent_diff > 20.0:
                return False

        return True

    def is_potentially_incorrect_match(self, target: Anime) -> bool:
        """Anime-only guard used by strategies.py before accepting a title/API match."""
        if self.id_mal > 0 and self.id_mal == target.id_mal:
            return False  # trusted: matched by MAL ID
        if self.id_mal == 0 and target.id_mal > 0 and not self.identical_title_match(target):
            return True  # source has no MAL ID, target has a different one, titles differ
        if (self.num_episodes in (0, 1)) and target.num_episodes > 4:
            if not self.identical_title_match(target):
                return True  # looks like a special/OVA matched to a full series
        return False

    def identical_title_match(self, target: Anime) -> bool:
        if self.title_en and self.title_en == target.title_en:
            return True
        if self.title_jp and self.title_jp == target.title_jp:
            return True
        return bool(self.title_romaji and self.title_romaji == target.title_romaji)

    def __str__(self) -> str:
        return (
            f"Anime{{IDAnilist: {self.id_anilist}, IDMal: {self.id_mal}, "
            f"TitleEN: {self.title_en}, TitleJP: {self.title_jp}, "
            f"MediaListStatus: {self.status.value}, Score: {self.score}, "
            f"Progress: {self.progress}, EpisodeNumber: {self.num_episodes}, "
            f"SeasonYear: {self.season_year}, StartedAt: {self.started_at}, "
            f"FinishedAt: {self.finished_at}}}"
        )

    @classmethod
    def from_anilist_entry(
        cls, entry: AniListListEntry, score_format: str, *, reverse: bool
    ) -> Anime:
        media = entry.media
        return cls(
            id_anilist=media.id,
            id_mal=media.id_mal or 0,
            title_en=media.title.english,
            title_jp=media.title.native,
            title_romaji=media.title.romaji,
            status=_anilist_status_to_anime_status(entry.status),
            score=normalize_score_for_mal(entry.score, score_format),
            progress=entry.progress,
            num_episodes=media.episodes or 0,
            season_year=media.season_year or 0,
            started_at=entry.started_at.to_date() if entry.started_at else None,
            finished_at=entry.completed_at.to_date() if entry.completed_at else None,
            is_favourite=media.is_favourite,
            is_reverse=reverse,
        )

    @classmethod
    def from_mal_entry(cls, entry: MALUserAnimeEntry, *, reverse: bool) -> Anime:
        anime, status = entry.anime, entry.status
        return cls(
            # Forward sync: this Anime is a target, -1 means "AniList ID
            # deliberately unknown, don't bother searching for it". Reverse
            # sync: this Anime is a source, 0 means "unknown, please look it
            # up" and triggers the strategy chain's name/API search.
            id_anilist=0 if reverse else -1,
            id_mal=anime.id,
            title_en=anime.alternative_titles.en or anime.title,
            title_jp=anime.alternative_titles.ja or anime.title,
            status=_mal_status_to_anime_status(status.status),
            score=status.score,  # MAL score is already 0-10 int
            progress=status.num_episodes_watched,
            num_episodes=anime.num_episodes,
            season_year=anime.start_season_year or 0,
            started_at=parse_mal_date(status.start_date),
            finished_at=parse_mal_date(status.finish_date),
            is_favourite=False,  # MAL API v2 doesn't expose favorites
            is_reverse=reverse,
        )

    @classmethod
    def from_anilist_media(cls, media: AniListMedia, *, reverse: bool) -> Anime:
        """Build a bare Anime from an AniList search/lookup result.

        No list-entry data (status/progress/score/dates) exists yet at this
        point; those get filled in from the other service's data by the caller.
        """
        return cls(
            id_anilist=media.id,
            id_mal=media.id_mal or 0,
            title_en=media.title.english,
            title_jp=media.title.native,
            title_romaji=media.title.romaji,
            num_episodes=media.episodes or 0,
            season_year=media.season_year or 0,
            is_reverse=reverse,
        )


# --------------------------------------------------------------------------
# Manga
# --------------------------------------------------------------------------


@dataclass
class Manga:
    id_anilist: int = 0
    id_mal: int = 0
    title_en: str = ""
    title_jp: str = ""
    title_romaji: str = ""
    status: MangaStatus = MangaStatus.UNKNOWN
    score: int = 0
    progress: int = 0
    progress_volumes: int = 0
    chapters: int = 0
    volumes: int = 0
    started_at: date | None = None
    finished_at: date | None = None
    is_favourite: bool = False
    is_reverse: bool = False

    def get_target_id(self) -> int:
        return self.id_anilist if self.is_reverse else self.id_mal

    def get_source_id(self) -> int:
        return self.id_mal if self.is_reverse else self.id_anilist

    def get_status_string(self) -> str:
        return self.status.value

    def get_title(self) -> str:
        return self.title_en or self.title_jp or self.title_romaji

    def get_string_diff_with_target(self, target: Target) -> str:
        if not isinstance(target, Manga):
            return "Diff{undefined}"
        return _build_diff_string(
            ("Status", self.status, target.status),
            ("Score", self.score, target.score),
            ("Progress", self.progress, target.progress),
            ("ProgressVolumes", self.progress_volumes, target.progress_volumes),
            ("StartedAt", self.started_at, target.started_at),
            ("FinishedAt", self.finished_at, target.finished_at),
        )

    def same_progress_with_target(self, target: Target) -> bool:
        if not isinstance(target, Manga):
            return False
        if self.status != target.status:
            return False
        if self.score != target.score:
            return False
        if self.progress != target.progress:
            return False
        if self.progress_volumes != target.progress_volumes:
            return False
        if not same_dates(self.started_at, target.started_at):
            return False
        if self.status == MangaStatus.COMPLETED and not same_dates(
            self.finished_at, target.finished_at
        ):
            return False
        return True

    def same_type_with_target(self, target: Target) -> bool:
        if not isinstance(target, Manga):
            return False
        if self.id_mal > 0 and target.id_mal > 0 and self.id_mal == target.id_mal:
            return True
        if self.id_anilist > 0 and target.id_anilist > 0 and self.id_anilist == target.id_anilist:
            return True
        if self.same_title_with_target(target):
            return True
        # Fallback: some manga are split across separate entries on one service
        # but merged into a single entry on the other. Matching chapter/volume
        # counts is a reasonable signal when titles don't line up.
        if (
            (self.chapters > 0 or self.volumes > 0)
            and self.chapters == target.chapters
            and self.volumes == target.volumes
        ):
            return True
        return False

    def same_title_with_target(self, target: Target) -> bool:
        if not isinstance(target, Manga):
            return False
        return _title_matching_levels(
            self.title_en, self.title_jp, self.title_romaji,
            target.title_en, target.title_jp, target.title_romaji,
        )

    def __str__(self) -> str:
        return (
            f"Manga{{IDAnilist: {self.id_anilist}, IDMal: {self.id_mal}, "
            f"TitleEN: {self.title_en}, TitleJP: {self.title_jp}, "
            f"Status: {self.status.value}, Score: {self.score}, "
            f"Progress: {self.progress}, ProgressVolumes: {self.progress_volumes}, "
            f"Chapters: {self.chapters}, Volumes: {self.volumes}, "
            f"StartedAt: {self.started_at}, FinishedAt: {self.finished_at}}}"
        )

    @classmethod
    def from_anilist_entry(
        cls, entry: AniListListEntry, score_format: str, *, reverse: bool
    ) -> Manga:
        media = entry.media
        return cls(
            id_anilist=media.id,
            id_mal=media.id_mal or 0,
            title_en=media.title.english,
            title_jp=media.title.native,
            title_romaji=media.title.romaji,
            status=_anilist_status_to_manga_status(entry.status),
            score=normalize_score_for_mal(entry.score, score_format),
            progress=entry.progress,
            progress_volumes=entry.progress_volumes,
            chapters=media.chapters or 0,
            volumes=media.volumes or 0,
            started_at=entry.started_at.to_date() if entry.started_at else None,
            finished_at=entry.completed_at.to_date() if entry.completed_at else None,
            is_favourite=media.is_favourite,
            is_reverse=reverse,
        )

    @classmethod
    def from_mal_entry(cls, entry: MALUserMangaEntry, *, reverse: bool) -> Manga:
        manga, status = entry.manga, entry.status
        return cls(
            id_anilist=0 if reverse else -1,  # see Anime.from_mal_entry for why
            id_mal=manga.id,
            title_en=manga.alternative_titles.en or manga.title,
            title_jp=manga.alternative_titles.ja or manga.title,
            status=_mal_status_to_manga_status(status.status),
            score=status.score,
            progress=status.num_chapters_read,
            progress_volumes=status.num_volumes_read,
            chapters=manga.num_chapters,
            volumes=manga.num_volumes,
            started_at=parse_mal_date(status.start_date),
            finished_at=parse_mal_date(status.finish_date),
            is_favourite=False,
            is_reverse=reverse,
        )

    @classmethod
    def from_anilist_media(cls, media: AniListMedia, *, reverse: bool) -> Manga:
        """Build a bare Manga from an AniList search/lookup result (see
        Anime.from_anilist_media for the same caveat about missing list data)."""
        return cls(
            id_anilist=media.id,
            id_mal=media.id_mal or 0,
            title_en=media.title.english,
            title_jp=media.title.native,
            title_romaji=media.title.romaji,
            chapters=media.chapters or 0,
            volumes=media.volumes or 0,
            is_reverse=reverse,
        )
