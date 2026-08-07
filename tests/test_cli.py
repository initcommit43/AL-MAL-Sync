"""Tests for cli.py: command wiring (login/logout/status/unmapped) against
faked OAuth/mappings, and `sync`/`watch --once` flag parsing against a
stubbed _run_sync_command. Sync orchestration itself (strategy chain
assembly, run_sync's return value) is tested in test_sync_runner.py."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from al_mal_sync import cli
from al_mal_sync.mapping.manual_mappings import MappingsConfig
from al_mal_sync.sync.updater import SyncOutcome
from al_mal_sync.unmapped import UnmappedRecord, UnmappedState


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every command loads config via _load_config -> load_config(); stub it
    so no config.yaml/env vars are required for CLI-wiring tests."""
    monkeypatch.setattr(cli, "load_config", lambda path=None: cli.Config())


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

        def _fake_run_sync_command(config: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        monkeypatch.setattr(cli, "_run_sync_command", _fake_run_sync_command)
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
        monkeypatch.setattr(cli, "_run_sync_command", lambda config, **kw: calls.append(kw))
        result = CliRunner().invoke(cli.main, ["watch", "--once"])
        assert result.exit_code == 0, result.output
        assert len(calls) == 1

    def test_watch_without_once_and_no_interval_or_schedule_fails_validation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_run_sync_command", lambda config, **kw: None)
        result = CliRunner().invoke(cli.main, ["watch"])
        assert result.exit_code != 0

    def test_watch_with_invalid_cron_expression_is_a_clean_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_run_sync_command", lambda config, **kw: None)
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
        monkeypatch.setattr(cli, "_run_sync_command", lambda config, **kw: calls.append(kw))

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
        monkeypatch.setattr(cli, "_run_sync_command", lambda config, **kw: calls.append(kw))

        class _StopLoop(Exception):
            pass

        def _fake_sleep(seconds: float) -> None:
            raise _StopLoop

        monkeypatch.setattr(cli.time, "sleep", _fake_sleep)
        result = CliRunner().invoke(cli.main, ["watch", "-i", "1h"])
        assert len(calls) == 1
        assert isinstance(result.exception, _StopLoop)


class TestExportAndImport:
    """CLI wiring for `export`/`import` against a stubbed run_export/run_import.
    Orchestration itself (matching, XML parsing) is tested in
    test_xml_sync.py and test_xml_list.py."""

    def test_export_writes_one_file_per_kind(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setattr(
            cli, "run_export",
            lambda config, **kw: {"anime": "<anime-xml/>", "manga": "<manga-xml/>"},
        )
        result = CliRunner().invoke(
            cli.main, ["export", "-s", "anilist", "--all", "--output-dir", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "anilist_anime.xml").read_text() == "<anime-xml/>"
        assert (tmp_path / "anilist_manga.xml").read_text() == "<manga-xml/>"

    def test_export_output_path_used_for_single_kind(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setattr(cli, "run_export", lambda config, **kw: {"anime": "<xml/>"})
        out_file = tmp_path / "mylist.xml"
        result = CliRunner().invoke(cli.main, ["export", "-s", "myanimelist", "-o", str(out_file)])
        assert result.exit_code == 0, result.output
        assert out_file.read_text() == "<xml/>"

    def test_export_rejects_output_with_all(self) -> None:
        result = CliRunner().invoke(cli.main, ["export", "-s", "anilist", "--all", "-o", "x.xml"])
        assert result.exit_code != 0
        assert "--output can't be combined with --all" in result.output

    def test_export_service_error_becomes_click_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(config: Any, **kw: Any) -> Any:
            raise cli.XmlSyncError("bad service")

        monkeypatch.setattr(cli, "run_export", _raise)
        result = CliRunner().invoke(cli.main, ["export", "-s", "anilist"])
        assert result.exit_code != 0
        assert "bad service" in result.output

    def test_import_forwards_flags_and_reads_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        captured: dict[str, Any] = {}

        def _fake_run_import(config: Any, **kw: Any) -> Any:
            captured.update(kw)
            return "anime", SyncOutcome()

        monkeypatch.setattr(cli, "run_import", _fake_run_import)
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")

        result = CliRunner().invoke(
            cli.main, ["import", "-i", str(xml_file), "-t", "anilist", "--force", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert captured["target_service"] == "anilist"
        assert captured["xml_text"] == "<myanimelist/>"
        assert captured["force"] is True
        assert captured["dry_run"] is True
        assert captured["kind"] is None

    def test_import_manga_flag_sets_kind(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        captured: dict[str, Any] = {}

        def _fake_run_import(config: Any, **kw: Any) -> Any:
            captured.update(kw)
            return "manga", SyncOutcome()

        monkeypatch.setattr(cli, "run_import", _fake_run_import)
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")

        result = CliRunner().invoke(cli.main, ["import", "-i", str(xml_file), "-t", "myanimelist", "--manga"])
        assert result.exit_code == 0, result.output
        assert captured["kind"] == "manga"

    def test_import_missing_file_is_a_clean_error(self) -> None:
        result = CliRunner().invoke(cli.main, ["import", "-i", "does-not-exist.xml", "-t", "anilist"])
        assert result.exit_code != 0


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
            MappingsConfig, "save", lambda self, path=None: saved_mappings.update(ids=self.ignore.anilist_ids)
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
            MappingsConfig, "save", lambda self, path=None: saved_mappings.update(ids=self.ignore.anilist_ids)
        )
        monkeypatch.setattr(cli, "save_unmapped_state", lambda state, path=None: None)

        result = CliRunner().invoke(cli.main, ["unmapped", "--fix"], input="i\n")
        assert result.exit_code == 0, result.output
        assert saved_mappings["ids"] == [10]
