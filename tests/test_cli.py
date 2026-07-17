"""Tests for cli.py: command wiring (login/logout/status/unmapped) against
faked OAuth/mappings, `sync`/`watch --once` flag parsing against a stubbed
_run_sync, and the pure _build_strategy_chain assembly logic in isolation."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from al_mal_sync import cli
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
from al_mal_sync.unmapped import UnmappedRecord, UnmappedState


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command loads config via _load_config -> load_config(); stub it
    so no config.yaml/env vars are required for CLI-wiring tests."""
    monkeypatch.setattr(cli, "load_config", lambda path=None: cli.Config())


class TestBuildStrategyChain:
    def test_anime_forward_order(self) -> None:
        chain = cli._build_strategy_chain(
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
        chain = cli._build_strategy_chain(
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
        chain = cli._build_strategy_chain(
            "anime", reverse=True, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        names = [type(s) for s in chain.strategies]
        assert names[-2:] == [MALIDStrategy, APISearchStrategy]

    def test_forward_direction_has_no_mal_id_strategy(self) -> None:
        chain = cli._build_strategy_chain(
            "anime", reverse=False, mappings=MappingsConfig(),
            offline_database=None, hato_client=None, arm_client=None, jikan_client=None,
            target_service=object(),
        )
        assert MALIDStrategy not in [type(s) for s in chain.strategies]


class TestLogin:
    def test_already_authenticated_skips_login_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeOAuth:
            needs_init = False

            def login(self, *a: Any, **kw: Any) -> None:
                raise AssertionError("should not be called")

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["login", "-s", "anilist"])
        assert result.exit_code == 0
        assert "already authenticated" in result.output

    def test_runs_login_flow_when_not_authenticated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []

        class _FakeOAuth:
            needs_init = True

            def login(self, port: str, **kw: Any) -> None:
                calls.append(port)

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["login", "-s", "myanimelist"])
        assert result.exit_code == 0
        assert calls == ["18080"]
        assert "login successful" in result.output

    def test_oauth_error_becomes_click_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeOAuth:
            needs_init = True

            def login(self, *a: Any, **kw: Any) -> None:
                raise cli.OAuthError("boom")

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["login", "-s", "anilist"])
        assert result.exit_code != 0
        assert "boom" in result.output


class TestLogoutAndStatus:
    def test_logout_all_deletes_both_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        deleted = []

        class _FakeOAuth:
            def delete_token(self) -> None:
                deleted.append(1)

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["logout"])
        assert result.exit_code == 0
        assert len(deleted) == 2

    def test_status_reports_not_authenticated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeOAuth:
            needs_init = True
            is_token_valid = False
            token_expiry = None

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["status"])
        assert result.exit_code == 0
        assert result.output.count("not authenticated") == 2

    def test_status_reports_valid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeOAuth:
            needs_init = False
            is_token_valid = True
            token_expiry = None

        monkeypatch.setattr(cli, "_oauth_for", lambda name, config: _FakeOAuth())
        result = CliRunner().invoke(cli.main, ["status"])
        assert result.exit_code == 0
        assert "authenticated (expires never)" in result.output


class TestSyncAndWatchFlagWiring:
    def test_sync_forwards_flags_to_run_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _fake_run_sync(config: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(cli, "_run_sync", _fake_run_sync)
        result = CliRunner().invoke(
            cli.main, ["sync", "--manga", "--dry-run", "--reverse-direction", "--favorites"]
        )
        assert result.exit_code == 0, result.output
        assert captured["manga"] is True
        assert captured["dry_run"] is True
        assert captured["reverse"] is True
        assert captured["favorites"] is True
        assert captured["all_media"] is False

    def test_watch_once_runs_single_sync_and_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []
        monkeypatch.setattr(cli, "_run_sync", lambda config, **kw: calls.append(kw))
        result = CliRunner().invoke(cli.main, ["watch", "--once"])
        assert result.exit_code == 0, result.output
        assert len(calls) == 1

    def test_watch_without_once_and_no_interval_or_schedule_fails_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_run_sync", lambda config, **kw: None)
        result = CliRunner().invoke(cli.main, ["watch"])
        assert result.exit_code != 0

    def test_watch_with_invalid_cron_expression_is_a_clean_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_run_sync", lambda config, **kw: None)
        # 5 fields (passes Phase 1's shape check) but minute=99 is out of range.
        result = CliRunner().invoke(cli.main, ["watch", "-s", "99 * * * *"])
        assert result.exit_code != 0
        assert "invalid watch schedule" in result.output

    def test_watch_with_schedule_waits_for_next_fire_time_before_syncing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cron mode waits for the next scheduled time before syncing (like
        crontab) -- unlike interval mode, it does not sync immediately on
        startup unless --once is also passed."""
        calls = []
        monkeypatch.setattr(cli, "_run_sync", lambda config, **kw: calls.append(kw))

        class _StopLoop(Exception):
            pass

        sleep_calls: list[float] = []

        def _fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) > 1:
                raise _StopLoop

        monkeypatch.setattr(cli.time, "sleep", _fake_sleep)
        result = CliRunner().invoke(cli.main, ["watch", "-s", "0 * * * *"])
        assert len(calls) == 1
        assert isinstance(result.exception, _StopLoop)
        assert "next sync at" in result.output

    def test_watch_with_interval_runs_loop_once_then_sleep_is_stubbed_to_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(cli, "_run_sync", lambda config, **kw: calls.append(kw))

        class _StopLoop(Exception):
            pass

        def _fake_sleep(seconds: float) -> None:
            raise _StopLoop

        monkeypatch.setattr(cli.time, "sleep", _fake_sleep)
        result = CliRunner().invoke(cli.main, ["watch", "-i", "1h"])
        assert len(calls) == 1
        assert isinstance(result.exception, _StopLoop)


class TestUnmapped:
    def _state_with_one_entry(self) -> UnmappedState:
        return UnmappedState(
            entries=[
                UnmappedRecord(
                    title="Some Show", anilist_id=10, mal_id=0, media_type="anime",
                    direction="forward", reason="no strategy matched", updated_at="2026-01-01T00:00:00+00:00",
                )
            ]
        )

    def test_no_entries_prints_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "load_unmapped_state", lambda path=None: UnmappedState())
        result = CliRunner().invoke(cli.main, ["unmapped"])
        assert result.exit_code == 0
        assert "No unmapped entries." in result.output

    def test_default_lists_entries_without_prompting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "load_unmapped_state", lambda path=None: self._state_with_one_entry())
        result = CliRunner().invoke(cli.main, ["unmapped"])
        assert result.exit_code == 0
        assert "Some Show" in result.output

    def test_ignore_all_adds_to_mappings_and_clears_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved_mappings: dict[str, Any] = {}
        saved_state: dict[str, Any] = {}

        monkeypatch.setattr(cli, "load_unmapped_state", lambda path=None: self._state_with_one_entry())
        monkeypatch.setattr(cli, "load_mappings", lambda path=None: MappingsConfig())
        monkeypatch.setattr(
            cli.MappingsConfig, "save", lambda self, path=None: saved_mappings.update(ids=self.ignore.anilist_ids)
        )
        monkeypatch.setattr(cli, "save_unmapped_state", lambda state, path=None: saved_state.update(entries=state.entries))

        result = CliRunner().invoke(cli.main, ["unmapped", "--ignore-all"])
        assert result.exit_code == 0, result.output
        assert saved_mappings["ids"] == [10]
        assert saved_state["entries"] == []

    def test_fix_ignore_by_id_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved_mappings: dict[str, Any] = {}
        monkeypatch.setattr(cli, "load_unmapped_state", lambda path=None: self._state_with_one_entry())
        monkeypatch.setattr(cli, "load_mappings", lambda path=None: MappingsConfig())
        monkeypatch.setattr(
            cli.MappingsConfig, "save", lambda self, path=None: saved_mappings.update(ids=self.ignore.anilist_ids)
        )
        monkeypatch.setattr(cli, "save_unmapped_state", lambda state, path=None: None)

        result = CliRunner().invoke(cli.main, ["unmapped", "--fix"], input="i\n")
        assert result.exit_code == 0, result.output
        assert saved_mappings["ids"] == [10]
