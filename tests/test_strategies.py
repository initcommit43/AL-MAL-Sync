"""Tests for the ID mapping strategy chain: each strategy's find/no-find
behavior, the "return a freshly-fetched target even if not in the user's list
yet" distinction that only MALIDStrategy/APISearchStrategy have, and the
should_reject_match guard shared by TitleStrategy/APISearchStrategy."""

from __future__ import annotations

from typing import Any

from al_mal_sync.mapping.jikan_api import JikanMangaData
from al_mal_sync.mapping.manual_mappings import MappingsConfig
from al_mal_sync.mapping.offline_database import AODEntry, OfflineDatabase
from al_mal_sync.mapping.strategies import (
    APISearchStrategy,
    ARMAPIStrategy,
    HatoAPIStrategy,
    IDStrategy,
    JikanAPIStrategy,
    MALIDStrategy,
    ManualMappingStrategy,
    OfflineDatabaseStrategy,
    StrategyChain,
    TitleStrategy,
    should_reject_match,
)
from al_mal_sync.models import Anime, Manga


def _anime(**overrides: Any) -> Anime:
    defaults: dict[str, Any] = {"id_mal": 1, "id_anilist": 1, "title_en": "Show"}
    defaults.update(overrides)
    return Anime(**defaults)


def _manga(**overrides: Any) -> Manga:
    defaults: dict[str, Any] = {"id_mal": 1, "id_anilist": 1, "title_en": "Manga"}
    defaults.update(overrides)
    return Manga(**defaults)


class _FixedStrategy:
    def __init__(self, name: str, target: Any | None) -> None:
        self.name = name
        self._target = target

    def find_target(self, src: Any, existing_targets: Any, reporter: Any = None) -> Any:
        return self._target


class TestStrategyChain:
    def test_returns_first_match_with_index_and_name(self) -> None:
        target = _anime(id_mal=5)
        chain = StrategyChain(
            [_FixedStrategy("Miss", None), _FixedStrategy("Hit", target), _FixedStrategy("Never", _anime())]
        )
        result = chain.find_target_with_meta(_anime(), {})
        assert result is not None
        assert result.target is target
        assert result.strategy_idx == 1
        assert result.strategy_name == "Hit"

    def test_returns_none_when_nothing_matches(self) -> None:
        chain = StrategyChain([_FixedStrategy("Miss", None)])
        assert chain.find_target_with_meta(_anime(), {}) is None


class TestManualMappingStrategy:
    def test_forward_match(self) -> None:
        mappings = MappingsConfig()
        mappings.add_manual_mapping(anilist_id=1, mal_id=5)
        target = _anime(id_mal=5)
        result = ManualMappingStrategy(mappings, reverse=False).find_target(
            _anime(id_anilist=1, id_mal=0), {5: target}
        )
        assert result is target

    def test_reverse_match(self) -> None:
        mappings = MappingsConfig()
        mappings.add_manual_mapping(anilist_id=1, mal_id=5)
        target = _anime(id_anilist=1, is_reverse=True)
        result = ManualMappingStrategy(mappings, reverse=True).find_target(
            _anime(id_mal=5, id_anilist=0, is_reverse=True), {1: target}
        )
        assert result is target

    def test_mapped_id_not_in_list_returns_none(self) -> None:
        mappings = MappingsConfig()
        mappings.add_manual_mapping(anilist_id=1, mal_id=5)
        result = ManualMappingStrategy(mappings, reverse=False).find_target(
            _anime(id_anilist=1), {}
        )
        assert result is None

    def test_no_mappings_config_returns_none(self) -> None:
        strategy = ManualMappingStrategy(None, reverse=False)
        assert strategy.find_target(_anime(id_anilist=1), {1: _anime()}) is None


class TestIDStrategy:
    def test_direct_match(self) -> None:
        target = _anime(id_mal=5)
        assert IDStrategy().find_target(_anime(id_mal=5), {5: target}) is target

    def test_no_match(self) -> None:
        assert IDStrategy().find_target(_anime(id_mal=5), {}) is None


class TestOfflineDatabaseStrategy:
    def test_matches_via_mal_id_lookup(self) -> None:
        db = OfflineDatabase.build_from_entries(
            [AODEntry(sources=["https://myanimelist.net/anime/5", "https://anilist.co/anime/100"])]
        )
        target = _anime(id_anilist=100)
        result = OfflineDatabaseStrategy(db).find_target(
            _anime(id_mal=5, id_anilist=0), {100: target}
        )
        assert result is target

    def test_manga_source_returns_none(self) -> None:
        db = OfflineDatabase.build_from_entries([])
        assert OfflineDatabaseStrategy(db).find_target(_manga(), {}) is None

    def test_no_database_returns_none(self) -> None:
        assert OfflineDatabaseStrategy(None).find_target(_anime(), {}) is None


class _FakeArmClient:
    def __init__(self, anilist_map: dict[int, int] | None = None) -> None:
        self._anilist_map = anilist_map or {}

    def get_anilist_id(self, mal_id: int) -> int | None:
        return self._anilist_map.get(mal_id)

    def get_mal_id(self, anilist_id: int) -> int | None:
        return None


class TestARMAPIStrategy:
    def test_matches_via_mal_id(self) -> None:
        client = _FakeArmClient(anilist_map={5: 100})
        target = _anime(id_anilist=100)
        result = ARMAPIStrategy(client).find_target(_anime(id_mal=5, id_anilist=0), {100: target})
        assert result is target

    def test_manga_source_returns_none(self) -> None:
        assert ARMAPIStrategy(_FakeArmClient()).find_target(_manga(), {}) is None

    def test_no_client_returns_none(self) -> None:
        assert ARMAPIStrategy(None).find_target(_anime(), {}) is None


class _FakeHatoClient:
    def __init__(self, anilist_map: dict[tuple[int, str], int] | None = None) -> None:
        self._anilist_map = anilist_map or {}

    def get_anilist_id(self, mal_id: int, media_type: str) -> int | None:
        return self._anilist_map.get((mal_id, media_type))

    def get_mal_id(self, anilist_id: int, media_type: str) -> int | None:
        return None


class TestHatoAPIStrategy:
    def test_matches_anime(self) -> None:
        client = _FakeHatoClient(anilist_map={(5, "anime"): 100})
        target = _anime(id_anilist=100)
        result = HatoAPIStrategy(client).find_target(_anime(id_mal=5, id_anilist=0), {100: target})
        assert result is target

    def test_matches_manga(self) -> None:
        client = _FakeHatoClient(anilist_map={(5, "manga"): 100})
        target = _manga(id_anilist=100)
        result = HatoAPIStrategy(client).find_target(_manga(id_mal=5, id_anilist=0), {100: target})
        assert result is target

    def test_no_client_returns_none(self) -> None:
        assert HatoAPIStrategy(None).find_target(_anime(), {}) is None


class TestShouldRejectMatch:
    def test_rejects_on_target_id_mismatch(self) -> None:
        assert should_reject_match(_anime(id_mal=1), _anime(id_mal=2)) is True

    def test_rejects_special_vs_series_for_anime(self) -> None:
        src = _anime(id_mal=0, id_anilist=0, num_episodes=1, title_en="A")
        target = _anime(id_mal=0, id_anilist=0, num_episodes=24, title_en="B")
        assert should_reject_match(src, target) is True

    def test_manga_is_never_rejected_by_the_anime_only_guard(self) -> None:
        src = _manga(id_mal=0, id_anilist=0, title_en="A")
        target = _manga(id_mal=0, id_anilist=0, title_en="B")
        assert should_reject_match(src, target) is False


class TestTitleStrategy:
    def test_exact_title_match(self) -> None:
        target = _anime(id_mal=0, id_anilist=0, title_en="Exact Title")
        src = _anime(id_mal=0, id_anilist=0, title_en="Exact Title")
        assert TitleStrategy().find_target(src, {1: target}) is target

    def test_fuzzy_match_rejected_by_incorrect_match_guard(self) -> None:
        # Titles agree after normalization (punctuation/case only), so
        # same_title_with_target passes, but this looks like a special (0
        # episodes known) matched to a full 24-episode series - should_reject_match
        # must veto it since the titles aren't byte-identical.
        src = _anime(id_mal=0, id_anilist=0, num_episodes=0, title_en="Show: Special!")
        target = _anime(id_mal=0, id_anilist=0, num_episodes=24, title_en="show special")
        assert TitleStrategy().find_target(src, {1: target}) is None

    def test_no_match_for_unrelated_titles(self) -> None:
        src = _anime(id_mal=0, id_anilist=0, title_en="Something")
        target = _anime(id_mal=0, id_anilist=0, title_en="Completely Different")
        assert TitleStrategy().find_target(src, {1: target}) is None


class _FakeJikanClient:
    def __init__(
        self,
        search_results: dict[str, list[JikanMangaData]] | None = None,
        manga_by_id: dict[int, JikanMangaData] | None = None,
    ) -> None:
        self._search_results = search_results or {}
        self._manga_by_id = manga_by_id or {}

    def search_manga(self, query: str) -> list[JikanMangaData]:
        return self._search_results.get(query, [])

    def get_manga_by_mal_id(self, mal_id: int) -> JikanMangaData | None:
        return self._manga_by_id.get(mal_id)


class TestJikanAPIStrategy:
    def test_forward_direction_finds_mal_id_via_search(self) -> None:
        jikan_data = JikanMangaData(mal_id=50, title="Berserk")
        client = _FakeJikanClient(search_results={"Berserk": [jikan_data]})
        src = _manga(id_mal=0, id_anilist=1, title_en="Berserk", title_romaji="Berserk")
        target = _manga(id_mal=50)
        result = JikanAPIStrategy(client).find_target(src, {50: target})
        assert result is target

    def test_reverse_direction_finds_anilist_target_via_title_match(self) -> None:
        jikan_data = JikanMangaData(mal_id=50, title="Berserk", title_english="Berserk")
        client = _FakeJikanClient(manga_by_id={50: jikan_data})
        src = _manga(id_mal=50, id_anilist=0, is_reverse=True)
        target = _manga(id_anilist=1, title_en="Berserk", is_reverse=True)
        result = JikanAPIStrategy(client).find_target(src, {1: target})
        assert result is target

    def test_anime_source_returns_none(self) -> None:
        assert JikanAPIStrategy(_FakeJikanClient()).find_target(_anime(), {}) is None

    def test_both_ids_present_returns_none(self) -> None:
        strategy = JikanAPIStrategy(_FakeJikanClient())
        assert strategy.find_target(_manga(id_mal=1, id_anilist=1), {}) is None


class _FakeMediaServiceWithMalId:
    def __init__(self, by_mal_id: dict[int, Any] | None = None) -> None:
        self._by_mal_id = by_mal_id or {}

    def get_by_mal_id(self, mal_id: int) -> Any | None:
        return self._by_mal_id.get(mal_id)

    def get_by_id(self, target_id: int) -> Any | None:
        return None

    def get_by_name(self, name: str) -> list[Any]:
        return []


class TestMALIDStrategy:
    def test_prefers_existing_list_entry_over_fresh_fetch(self) -> None:
        # is_reverse=True on the fetched target matters: get_target_id() reads
        # id_anilist only in reverse mode, matching how MALIDStrategy is only
        # ever used in the reverse (MAL->AniList) direction.
        fetched = _anime(id_anilist=100, is_reverse=True)
        service = _FakeMediaServiceWithMalId(by_mal_id={5: fetched})
        existing = _anime(id_anilist=100, progress=5, is_reverse=True)
        src = _anime(id_mal=5, id_anilist=0, is_reverse=True)
        result = MALIDStrategy(service).find_target(src, {100: existing})
        assert result is existing

    def test_returns_freshly_fetched_target_when_not_yet_in_list(self) -> None:
        fetched = _anime(id_anilist=100, is_reverse=True)
        service = _FakeMediaServiceWithMalId(by_mal_id={5: fetched})
        src = _anime(id_mal=5, id_anilist=0, is_reverse=True)
        result = MALIDStrategy(service).find_target(src, {})
        assert result is fetched

    def test_zero_source_id_returns_none(self) -> None:
        strategy = MALIDStrategy(_FakeMediaServiceWithMalId())
        assert strategy.find_target(_anime(id_mal=0, is_reverse=True), {}) is None


class _FakeMediaService:
    def __init__(
        self, by_id: dict[int, Any] | None = None, by_name: dict[str, list[Any]] | None = None
    ) -> None:
        self._by_id = by_id or {}
        self._by_name = by_name or {}

    def get_by_id(self, target_id: int) -> Any | None:
        return self._by_id.get(target_id)

    def get_by_name(self, name: str) -> list[Any]:
        return self._by_name.get(name, [])


class TestAPISearchStrategy:
    def test_id_lookup_prefers_existing_list_entry(self) -> None:
        fetched = _anime(id_mal=5)
        service = _FakeMediaService(by_id={5: fetched})
        existing = _anime(id_mal=5, progress=3)
        result = APISearchStrategy(service).find_target(_anime(id_mal=5, id_anilist=0), {5: existing})
        assert result is existing

    def test_id_lookup_returns_fresh_target_when_not_yet_in_list(self) -> None:
        fetched = _anime(id_mal=5)
        service = _FakeMediaService(by_id={5: fetched})
        result = APISearchStrategy(service).find_target(_anime(id_mal=5, id_anilist=0), {})
        assert result is fetched

    def test_id_lookup_not_found_returns_none(self) -> None:
        strategy = APISearchStrategy(_FakeMediaService())
        assert strategy.find_target(_anime(id_mal=5, id_anilist=0), {}) is None

    def test_name_search_finds_new_candidate_by_type_match(self) -> None:
        candidate = _anime(id_mal=0, id_anilist=0, title_en="Found Show")
        service = _FakeMediaService(by_name={"Found Show": [candidate]})
        src = _anime(id_mal=0, id_anilist=0, title_en="Found Show")
        result = APISearchStrategy(service).find_target(src, {})
        assert result is candidate

    def test_name_search_rejects_bad_match_against_existing_list(self) -> None:
        # The candidate's ID happens to collide with something already in the
        # user's list, but the titles don't actually agree - must not accept it.
        candidate = _anime(id_mal=7, title_en="Candidate")
        existing = _anime(id_mal=7, title_en="Totally Different")
        service = _FakeMediaService(by_name={"Some Query": [candidate]})
        src = _anime(id_mal=0, id_anilist=0, title_en="Some Query")
        result = APISearchStrategy(service).find_target(src, {7: existing})
        assert result is None
