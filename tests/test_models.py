"""Tests for the Anime/Manga domain models: matching logic, progress comparison,
and construction from raw API client responses.

Title-matching helpers are tested directly (not only through same_title_with_target)
since the exact thresholds and normalization rules are easy to silently drift from
the reference tool while still "looking right" in casual testing.
"""

from __future__ import annotations

from datetime import date

from al_mal_sync.clients.anilist import AniListDate, AniListMedia, AniListTitle, AniListListEntry
from al_mal_sync.clients.myanimelist import (
    MALAnime,
    MALAnimeListStatus,
    MALTitles,
    MALUserAnimeEntry,
    MALUserMangaEntry,
    MALManga,
    MALMangaListStatus,
)
from al_mal_sync.models import (
    Anime,
    AnimeStatus,
    Manga,
    MangaStatus,
    normalize_title,
    title_matching_levels,
)
from al_mal_sync.sync.score import POINT_100


def _anime(**overrides: object) -> Anime:
    defaults: dict[str, object] = {"title_en": "Cowboy Bebop", "id_mal": 1, "id_anilist": 1}
    defaults.update(overrides)
    return Anime(**defaults)  # type: ignore[arg-type]


class TestTitleMatching:
    def test_normalize_strips_brackets_punctuation_and_case(self) -> None:
        assert normalize_title("Attack on Titan (TV)!") == "attack on titan"

    def test_exact_match_after_normalization(self) -> None:
        assert title_matching_levels("Attack on Titan!", "", "", "attack on titan", "", "")

    def test_fuzzy_match_on_shared_words(self) -> None:
        assert title_matching_levels(
            "Fullmetal Alchemist Brotherhood", "", "",
            "Fullmetal Alchemist: Brotherhood", "", "",
        )

    def test_no_match_for_unrelated_titles(self) -> None:
        assert not title_matching_levels("Cowboy Bebop", "", "", "Naruto", "", "")


class TestAnimeProgress:
    def test_identical_entries_are_same(self) -> None:
        a = _anime(status=AnimeStatus.WATCHING, score=8, progress=5, num_episodes=12)
        b = _anime(status=AnimeStatus.WATCHING, score=8, progress=5, num_episodes=12)
        assert a.same_progress_with_target(b) is True

    def test_different_status_is_not_same(self) -> None:
        a = _anime(status=AnimeStatus.WATCHING)
        b = _anime(status=AnimeStatus.COMPLETED)
        assert a.same_progress_with_target(b) is False

    def test_finished_at_ignored_unless_completed(self) -> None:
        a = _anime(status=AnimeStatus.WATCHING, finished_at=date(2020, 1, 1))
        b = _anime(status=AnimeStatus.WATCHING, finished_at=date(2021, 6, 6))
        assert a.same_progress_with_target(b) is True

    def test_finished_at_compared_when_completed(self) -> None:
        a = _anime(status=AnimeStatus.COMPLETED, finished_at=date(2020, 1, 1))
        b = _anime(status=AnimeStatus.COMPLETED, finished_at=date(2021, 6, 6))
        assert a.same_progress_with_target(b) is False

    def test_matching_remaining_episodes_counts_as_same(self) -> None:
        # Source thinks the show has 12 episodes and is 2 behind; target thinks
        # it has 13 (season still airing) and is also 2 behind. Same "remaining"
        # gap, so treat progress as in sync rather than looping forever.
        a = _anime(num_episodes=12, progress=10)
        b = _anime(num_episodes=13, progress=11)
        assert a.same_progress_with_target(b) is True

    def test_source_never_overwrites_existing_target_date(self) -> None:
        a = _anime(started_at=None)
        b = _anime(started_at=date(2020, 1, 1))
        assert a.same_progress_with_target(b) is True


class TestAnimeSameType:
    def test_matches_by_mal_id_even_with_different_titles(self) -> None:
        a = _anime(id_mal=42, title_en="Old Title")
        b = _anime(id_mal=42, title_en="New Title")
        assert a.same_type_with_target(b) is True

    def test_falls_back_to_title_match_without_ids(self) -> None:
        a = _anime(id_mal=0, id_anilist=0, title_en="Cowboy Bebop")
        b = _anime(id_mal=0, id_anilist=0, title_en="Cowboy Bebop")
        assert a.same_type_with_target(b) is True

    def test_rejects_title_match_with_wildly_different_episode_counts(self) -> None:
        a = _anime(id_mal=0, id_anilist=0, title_en="Show", num_episodes=12)
        b = _anime(id_mal=0, id_anilist=0, title_en="Show", num_episodes=1)
        assert a.same_title_with_target(b) is False


class TestAnimeIncorrectMatch:
    def test_trusted_when_mal_ids_match(self) -> None:
        a = _anime(id_mal=1)
        b = _anime(id_mal=1)
        assert a.is_potentially_incorrect_match(b) is False

    def test_rejected_when_no_mal_id_and_titles_differ(self) -> None:
        a = _anime(id_mal=0, title_en="Show A")
        b = _anime(id_mal=99, title_en="Show B")
        assert a.is_potentially_incorrect_match(b) is True

    def test_special_vs_series_episode_mismatch_rejected(self) -> None:
        a = _anime(id_mal=0, title_en="Show A", num_episodes=1)
        b = _anime(id_mal=0, title_en="Show B", num_episodes=24)
        assert a.is_potentially_incorrect_match(b) is True

    def test_identical_titles_override_episode_mismatch(self) -> None:
        a = _anime(id_mal=0, title_en="Show", num_episodes=1)
        b = _anime(id_mal=0, title_en="Show", num_episodes=24)
        assert a.is_potentially_incorrect_match(b) is False


class TestAnimeFromAniListEntry:
    def test_maps_fields_and_normalizes_score(self) -> None:
        entry = AniListListEntry(
            id=1,
            status="CURRENT",
            score=85.0,
            progress=5,
            started_at=AniListDate(2024, 1, 1),
            completed_at=None,
            media=AniListMedia(
                id=100,
                id_mal=200,
                title=AniListTitle(romaji="R", english="E", native="N"),
                episodes=12,
                season_year=2024,
                is_favourite=True,
            ),
        )

        anime = Anime.from_anilist_entry(entry, POINT_100, reverse=False)

        assert anime.id_anilist == 100
        assert anime.id_mal == 200
        assert anime.status == AnimeStatus.WATCHING
        assert anime.score == 9  # 85 on POINT_100 -> 8.5 -> half-up 9
        assert anime.started_at == date(2024, 1, 1)
        assert anime.finished_at is None
        assert anime.is_favourite is True


class TestAnimeFromMalEntry:
    def test_forward_sync_uses_sentinel_anilist_id(self) -> None:
        entry = MALUserAnimeEntry(
            anime=MALAnime(id=5, title="Show", alternative_titles=MALTitles()),
            status=MALAnimeListStatus(status="watching", num_episodes_watched=3),
        )
        anime = Anime.from_mal_entry(entry, reverse=False)
        assert anime.id_anilist == -1
        assert anime.get_target_id() == 5  # forward: target id is the MAL id

    def test_reverse_sync_uses_zero_to_trigger_lookup(self) -> None:
        entry = MALUserAnimeEntry(
            anime=MALAnime(id=5, title="Show", alternative_titles=MALTitles()),
            status=MALAnimeListStatus(status="watching"),
        )
        anime = Anime.from_mal_entry(entry, reverse=True)
        assert anime.id_anilist == 0
        assert anime.get_source_id() == 5  # reverse: source id is the MAL id

    def test_alternative_title_falls_back_to_canonical_title(self) -> None:
        entry = MALUserAnimeEntry(
            anime=MALAnime(id=5, title="Canonical", alternative_titles=MALTitles(en="", ja="")),
            status=MALAnimeListStatus(status="watching"),
        )
        anime = Anime.from_mal_entry(entry, reverse=False)
        assert anime.title_en == "Canonical"


class TestMangaSameType:
    def test_falls_back_to_chapters_and_volumes_when_titles_differ(self) -> None:
        a = Manga(id_mal=0, id_anilist=0, title_en="Title A", chapters=50, volumes=5)
        b = Manga(id_mal=0, id_anilist=0, title_en="Title B", chapters=50, volumes=5)
        assert a.same_type_with_target(b) is True

    def test_no_match_when_titles_and_counts_both_differ(self) -> None:
        a = Manga(id_mal=0, id_anilist=0, title_en="Title A", chapters=50, volumes=5)
        b = Manga(id_mal=0, id_anilist=0, title_en="Title B", chapters=10, volumes=1)
        assert a.same_type_with_target(b) is False


class TestMangaFromMalEntry:
    def test_maps_chapters_and_volumes_read(self) -> None:
        entry = MALUserMangaEntry(
            manga=MALManga(id=7, title="Berserk", alternative_titles=MALTitles()),
            status=MALMangaListStatus(
                status="reading", num_chapters_read=50, num_volumes_read=5
            ),
        )
        manga = Manga.from_mal_entry(entry, reverse=False)
        assert manga.progress == 50
        assert manga.progress_volumes == 5
        assert manga.status == MangaStatus.READING
