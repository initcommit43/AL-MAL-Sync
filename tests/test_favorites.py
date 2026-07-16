"""Tests for favorites sync: id-mapping extraction from resolved matches, the
MAL -> AniList write path (add-missing, skip-already-favourited, never
un-favourite), and the AniList -> MAL report-only comparison."""

from __future__ import annotations

from typing import Any

from al_mal_sync.mapping.strategies import MatchResult
from al_mal_sync.models import Anime, Manga
from al_mal_sync.sync.conflict import ResolvedMatch
from al_mal_sync.sync.favorites import (
    build_id_mapping,
    check_anilist_favorites_against_mal,
    sync_mal_favorites_to_anilist,
)


def _anime(**overrides: Any) -> Anime:
    defaults: dict[str, Any] = {"id_mal": 1, "id_anilist": 1, "title_en": "Show"}
    defaults.update(overrides)
    return Anime(**defaults)


class _FakeAniListClient:
    def __init__(self, *, raise_for: set[int] | None = None) -> None:
        self.calls: list[tuple[int, int]] = []  # (anime_id, manga_id), 0 means unset
        self._raise_for = raise_for or set()

    def toggle_favourite(self, *, anime_id: int = 0, manga_id: int = 0) -> None:
        target_id = anime_id or manga_id
        if target_id in self._raise_for:
            raise RuntimeError("toggle failed")
        self.calls.append((anime_id, manga_id))


class TestBuildIdMapping:
    def test_forward_direction_maps_anilist_to_mal(self) -> None:
        # Forward outcome: source is the AniList entry, target is MAL.
        source = _anime(id_anilist=10, id_mal=0, is_reverse=False)
        target = _anime(id_mal=20, is_reverse=False)
        matched = [ResolvedMatch(source, MatchResult(target=target, strategy_name="IDStrategy", strategy_idx=0))]

        assert build_id_mapping(matched) == {10: 20}

    def test_reverse_direction_maps_mal_to_anilist(self) -> None:
        # Reverse outcome: source is the MAL entry, target is AniList.
        source = _anime(id_mal=20, id_anilist=0, is_reverse=True)
        target = _anime(id_anilist=10, is_reverse=True)
        matched = [ResolvedMatch(source, MatchResult(target=target, strategy_name="IDStrategy", strategy_idx=0))]

        assert build_id_mapping(matched) == {20: 10}


class TestSyncMalFavoritesToAnilist:
    def test_adds_missing_favorite(self) -> None:
        client = _FakeAniListClient()
        outcome = sync_mal_favorites_to_anilist(
            {5}, {}, {5: 100}, client, media_kind="anime"
        )
        assert outcome.added_to_anilist == [100]
        assert client.calls == [(100, 0)]

    def test_uses_manga_id_kwarg_for_manga(self) -> None:
        client = _FakeAniListClient()
        outcome = sync_mal_favorites_to_anilist(
            {5}, {}, {5: 100}, client, media_kind="manga"
        )
        assert outcome.added_to_anilist == [100]
        assert client.calls == [(0, 100)]

    def test_skips_already_favourited_without_toggling(self) -> None:
        client = _FakeAniListClient()
        target = _anime(id_anilist=100, is_favourite=True)
        outcome = sync_mal_favorites_to_anilist(
            {5}, {100: target}, {5: 100}, client, media_kind="anime"
        )
        assert outcome.already_favourited == [100]
        assert outcome.added_to_anilist == []
        assert client.calls == []

    def test_unmapped_mal_id_is_reported_not_toggled(self) -> None:
        client = _FakeAniListClient()
        outcome = sync_mal_favorites_to_anilist({5}, {}, {}, client, media_kind="anime")
        assert outcome.unmapped == [5]
        assert client.calls == []

    def test_toggle_error_is_recorded_and_does_not_raise(self) -> None:
        client = _FakeAniListClient(raise_for={100})
        outcome = sync_mal_favorites_to_anilist(
            {5}, {}, {5: 100}, client, media_kind="anime"
        )
        assert outcome.added_to_anilist == []
        assert len(outcome.errors) == 1
        assert outcome.errors[0][0] == 100

    def test_target_not_yet_in_list_is_treated_as_not_favourited(self) -> None:
        client = _FakeAniListClient()
        outcome = sync_mal_favorites_to_anilist(
            {5}, {999: _anime(id_anilist=999)}, {5: 100}, client, media_kind="anime"
        )
        assert outcome.added_to_anilist == [100]


class TestCheckAnilistFavoritesAgainstMal:
    def test_matching_favorite_is_not_reported(self) -> None:
        entry = _anime(id_anilist=10, is_favourite=True)
        outcome = check_anilist_favorites_against_mal([entry], {20}, {10: 20})
        assert outcome.mismatched == []
        assert outcome.unmapped == []

    def test_mal_missing_favorite_is_mismatched(self) -> None:
        entry = _anime(id_anilist=10, is_favourite=True)
        outcome = check_anilist_favorites_against_mal([entry], set(), {10: 20})
        assert outcome.mismatched == [(10, 20)]

    def test_unmapped_favorite_is_reported_separately(self) -> None:
        entry = _anime(id_anilist=10, is_favourite=True)
        outcome = check_anilist_favorites_against_mal([entry], set(), {})
        assert outcome.unmapped == [10]
        assert outcome.mismatched == []

    def test_non_favorite_entries_are_ignored(self) -> None:
        entry = _anime(id_anilist=10, is_favourite=False)
        outcome = check_anilist_favorites_against_mal([entry], set(), {})
        assert outcome.mismatched == []
        assert outcome.unmapped == []

    def test_works_for_manga_entries_too(self) -> None:
        entry = Manga(id_anilist=10, id_mal=1, title_en="Manga", is_favourite=True)
        outcome = check_anilist_favorites_against_mal([entry], {20}, {10: 20})
        assert outcome.mismatched == []
