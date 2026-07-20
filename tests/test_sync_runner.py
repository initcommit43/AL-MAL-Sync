"""Tests for sync/runner.py: the pure build_strategy_chain assembly logic in
isolation, and run_sync()'s return-value/callback contract against faked
AniList/MyAnimeList clients (no real network/OAuth/cache access)."""

from __future__ import annotations

from typing import Any

import pytest

from al_mal_sync.config import Config
from al_mal_sync.mapping.manual_mappings import MappingsConfig
from al_mal_sync.mapping.strategies import (
    APISearchStrategy,
    ARMAPIStrategy,
    HatoAPIStrategy,
    IDStrategy,
    JikanAPIStrategy,
    MALIDStrategy,
    ManualMappingStrategy,
    OfflineDatabaseStrategy,
    TitleStrategy,
)
from al_mal_sync.sync import runner
from al_mal_sync.sync.updater import SyncOutcome
from al_mal_sync.unmapped import UnmappedState


class TestBuildStrategyChain:
    def test_anime_forward_order(self) -> None:
        chain = runner.build_strategy_chain(
            "anime", reverse=False, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        names = [type(s) for s in chain.strategies]
        assert names == [
            ManualMappingStrategy, IDStrategy, OfflineDatabaseStrategy,
            HatoAPIStrategy, ARMAPIStrategy, TitleStrategy, APISearchStrategy,
        ]

    def test_manga_forward_order_has_no_offline_db_or_arm(self) -> None:
        chain = runner.build_strategy_chain(
            "manga", reverse=False, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        names = [type(s) for s in chain.strategies]
        assert names == [
            ManualMappingStrategy, IDStrategy, HatoAPIStrategy,
            TitleStrategy, JikanAPIStrategy, APISearchStrategy,
        ]

    def test_reverse_direction_inserts_mal_id_strategy_before_api_search(self) -> None:
        chain = runner.build_strategy_chain(
            "anime", reverse=True, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        names = [type(s) for s in chain.strategies]
        assert names[-2:] == [MALIDStrategy, APISearchStrategy]

    def test_forward_direction_has_no_mal_id_strategy(self) -> None:
        chain = runner.build_strategy_chain(
            "anime", reverse=False, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        assert MALIDStrategy not in [type(s) for s in chain.strategies]


class _FakeAniListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_score_format(self) -> str:
        return "POINT_10"

    def get_user_anime_list(self) -> list[Any]:
        return []

    def get_user_manga_list(self) -> list[Any]:
        return []


class _FakeMyAnimeListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[Any]:
        return []

    def get_user_manga_list(self) -> list[Any]:
        return []


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_sync() is the real orchestrator; stub everything it touches
    outside the pure resolve/dedupe/process pipeline (OAuth, HTTP clients,
    disk-backed mappings/unmapped state) so this test exercises the wiring
    and return contract without real network/filesystem access."""
    monkeypatch.setattr(runner, "create_anilist_oauth", lambda config: object())
    monkeypatch.setattr(runner, "create_myanimelist_oauth", lambda config: object())
    monkeypatch.setattr(runner, "AniListClient", _FakeAniListClient)
    monkeypatch.setattr(runner, "MyAnimeListClient", _FakeMyAnimeListClient)
    monkeypatch.setattr(runner, "load_mappings", lambda path=None: MappingsConfig())
    monkeypatch.setattr(runner, "load_unmapped_state", lambda path=None: UnmappedState())
    monkeypatch.setattr(runner, "save_unmapped_state", lambda state, path=None: None)
    monkeypatch.setattr(runner, "save_sync_history", lambda entry, path=None: None)


def _config() -> Config:
    # Disable every optional id-mapping source so run_sync doesn't try to
    # build a real OfflineDatabase/HatoApiClient/etc. for this test.
    config = Config()
    config.offline_database.enabled = False
    config.hato_api.enabled = False
    config.arm_api.enabled = False
    config.jikan_api.enabled = False
    return config


class TestRunSync:
    def test_returns_outcomes_and_favorites_outcomes_tuple(self) -> None:
        outcomes, favorites_outcomes = runner.run_sync(
            _config(),
            force=False, dry_run=False, manga=False, all_media=False, reverse=False,
            offline_db=False, offline_db_force_refresh=False,
            arm_api=False, arm_api_url=None, jikan_api=False, favorites=False,
        )
        assert set(outcomes) == {"anime"}
        assert isinstance(outcomes["anime"], SyncOutcome)
        assert favorites_outcomes == {}

    def test_on_kind_start_called_once_per_requested_kind(self) -> None:
        calls: list[tuple[str, bool]] = []
        runner.run_sync(
            _config(),
            force=False, dry_run=False, manga=False, all_media=True, reverse=False,
            offline_db=False, offline_db_force_refresh=False,
            arm_api=False, arm_api_url=None, jikan_api=False, favorites=False,
            on_kind_start=lambda kind, reverse: calls.append((kind, reverse)),
        )
        assert calls == [("anime", False), ("manga", False)]

    def test_on_progress_is_optional(self) -> None:
        # No on_progress passed -- must not raise even though there are zero
        # resolved matches to report progress on.
        outcomes, _ = runner.run_sync(
            _config(),
            force=False, dry_run=False, manga=False, all_media=False, reverse=False,
            offline_db=False, offline_db_force_refresh=False,
            arm_api=False, arm_api_url=None, jikan_api=False, favorites=False,
        )
        assert outcomes["anime"].updated == []

    def test_persists_sync_history_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Any] = []
        monkeypatch.setattr(
            runner, "save_sync_history", lambda entry, path=None: calls.append((entry, path))
        )

        config = _config()
        runner.run_sync(
            config,
            force=False, dry_run=False, manga=False, all_media=True, reverse=False,
            offline_db=False, offline_db_force_refresh=False,
            arm_api=False, arm_api_url=None, jikan_api=False, favorites=False,
        )

        assert len(calls) == 1
        entry, path = calls[0]
        assert set(entry.per_kind) == {"anime", "manga"}
        assert path == config.resolved_sync_history_path
