"""Tests for the Updater's resolve -> deduplicate -> process pipeline:
matching via the strategy chain, --force direct-id mode, dry-run, ignore-list
filtering, per-entry error capture, and dedup integration."""

from __future__ import annotations

from typing import Any

from al_mal_sync.mapping.manual_mappings import MappingsConfig
from al_mal_sync.mapping.strategies import StrategyChain
from al_mal_sync.models import Anime, AnimeStatus
from al_mal_sync.sync.updater import Updater


def _anime(**overrides: Any) -> Anime:
    defaults: dict[str, Any] = {"id_mal": 1, "id_anilist": 1, "title_en": "Show"}
    defaults.update(overrides)
    return Anime(**defaults)


class _StubStrategy:
    """Matches sources to targets by object identity, set up per test."""

    name = "Stub"

    def __init__(self, mapping: dict[int, Any]) -> None:
        self._mapping = mapping

    def find_target(self, src: Any, existing_targets: Any, reporter: Any = None) -> Any:
        return self._mapping.get(id(src))


class _FakeTargetService:
    def __init__(self, *, raise_for_target_ids: set[int] | None = None) -> None:
        self.update_calls: list[tuple[Any, int]] = []
        self._raise_for = raise_for_target_ids or set()

    def update(self, source: Any, target_id: int) -> None:
        if target_id in self._raise_for:
            raise RuntimeError("update failed")
        self.update_calls.append((source, target_id))


def _chain_for(*pairs: tuple[Any, Any]) -> StrategyChain:
    mapping = {id(source): target for source, target in pairs}
    return StrategyChain([_StubStrategy(mapping)])


class TestUpdaterProcessing:
    def test_updates_when_progress_differs(self) -> None:
        source = _anime(progress=5, status=AnimeStatus.WATCHING)
        target = _anime(progress=0, status=AnimeStatus.WATCHING, id_mal=99)
        service = _FakeTargetService()
        updater = Updater(_chain_for((source, target)), service)

        outcome = updater.run([source], {})

        assert outcome.updated == [source]
        assert service.update_calls == [(source, target.get_target_id())]
        assert outcome.skipped == []

    def test_skips_when_already_in_sync(self) -> None:
        source = _anime(progress=5, status=AnimeStatus.WATCHING)
        target = _anime(progress=5, status=AnimeStatus.WATCHING)
        service = _FakeTargetService()
        updater = Updater(_chain_for((source, target)), service)

        outcome = updater.run([source], {})

        assert outcome.skipped == [source]
        assert service.update_calls == []

    def test_dry_run_does_not_call_update(self) -> None:
        source = _anime(progress=5)
        target = _anime(progress=0)
        service = _FakeTargetService()
        updater = Updater(_chain_for((source, target)), service, dry_run=True)

        outcome = updater.run([source], {})

        assert outcome.dry_run == [source]
        assert service.update_calls == []
        assert outcome.updated == []

    def test_unmatched_when_no_strategy_matches(self) -> None:
        source = _anime()
        service = _FakeTargetService()
        updater = Updater(StrategyChain([_StubStrategy({})]), service)

        outcome = updater.run([source], {})

        assert len(outcome.unmatched) == 1
        assert outcome.unmatched[0].source is source

    def test_error_during_update_is_recorded_and_does_not_raise(self) -> None:
        source = _anime(progress=5)
        target = _anime(progress=0, id_mal=42)
        service = _FakeTargetService(raise_for_target_ids={42})
        updater = Updater(_chain_for((source, target)), service)

        outcome = updater.run([source], {})

        assert outcome.updated == []
        assert len(outcome.errors) == 1
        assert outcome.errors[0][0] is source


class TestUpdaterForceMode:
    def test_force_skips_strategy_matching_and_looks_up_by_id_directly(self) -> None:
        source = _anime(id_mal=5, progress=5)
        target = _anime(id_mal=5, progress=0)
        service = _FakeTargetService()
        # Empty chain: if force mode consulted it, this test would fail to match.
        updater = Updater(StrategyChain([]), service, force=True)

        outcome = updater.run([source], {5: target})

        assert outcome.updated == [source]

    def test_force_with_no_target_id_in_existing_list_is_unmatched(self) -> None:
        source = _anime(id_mal=5)
        service = _FakeTargetService()
        updater = Updater(StrategyChain([]), service, force=True)

        outcome = updater.run([source], {})

        assert len(outcome.unmatched) == 1


class TestUpdaterIgnoreList:
    def test_forward_direction_ignored_by_anilist_id(self) -> None:
        mappings = MappingsConfig()
        mappings.add_ignore_by_id(1)
        source = _anime(id_anilist=1, is_reverse=False)
        target = _anime(id_mal=99)
        service = _FakeTargetService()
        updater = Updater(_chain_for((source, target)), service, mappings=mappings)

        outcome = updater.run([source], {})

        assert outcome.updated == []
        assert outcome.skipped == []
        assert outcome.unmatched == []
        assert service.update_calls == []

    def test_reverse_direction_ignored_by_mal_id(self) -> None:
        mappings = MappingsConfig()
        mappings.add_ignore_by_mal_id(7)
        source = _anime(id_mal=7, id_anilist=0, is_reverse=True)
        target = _anime(id_anilist=100, is_reverse=True)
        service = _FakeTargetService()
        updater = Updater(_chain_for((source, target)), service, mappings=mappings)

        outcome = updater.run([source], {})

        assert service.update_calls == []
        assert outcome.unmatched == []


class TestUpdaterDedupIntegration:
    def test_duplicate_matches_produce_one_update_and_one_conflict(self) -> None:
        target = _anime(id_mal=5, progress=0)
        source_a = _anime(id_anilist=1, progress=5, title_en="A")
        source_b = _anime(id_anilist=2, progress=5, title_en="B")
        service = _FakeTargetService()
        chain = _chain_for((source_a, target), (source_b, target))
        updater = Updater(chain, service)

        outcome = updater.run([source_a, source_b], {})

        assert len(service.update_calls) == 1
        assert len(outcome.conflicts) == 1
