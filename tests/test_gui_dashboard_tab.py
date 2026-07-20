"""Tests for gui/tabs/dashboard_tab.py: worker-thread wiring for the live
stats fetch, rendering across authenticated/not-authenticated/error states,
and the local last-sync/needs-attention reads. fetch_dashboard_stats is
monkeypatched at the dashboard_tab module level -- these tests exercise the
tab's own rendering and signal wiring, not the real API calls (that's
dashboard.py's job, covered in test_dashboard.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.dashboard import DashboardStats, PlatformStatus  # noqa: E402
from al_mal_sync.gui.tabs import dashboard_tab as dashboard_tab_module  # noqa: E402
from al_mal_sync.gui.tabs.dashboard_tab import DashboardTab  # noqa: E402
from al_mal_sync.sync_history import SyncHistoryEntry, save_sync_history  # noqa: E402
from al_mal_sync.unmapped import UnmappedRecord, UnmappedState, save_unmapped_state  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.

_BOTH_LOGGED_OUT = DashboardStats(
    anilist=PlatformStatus(authenticated=False), myanimelist=PlatformStatus(authenticated=False)
)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    cfg = Config()
    cfg.mappings_file_path = str(tmp_path / "mappings.yaml")
    unmapped_path = str(tmp_path / "unmapped.json")
    history_path = str(tmp_path / "sync_history.json")
    # Both resolved_*_path properties always call their module-level
    # default_*_path() (no per-Config override field) -- same pattern as
    # test_gui_mapping_issues_tab.py's config fixture.
    monkeypatch.setattr("al_mal_sync.config.default_unmapped_state_path", lambda: unmapped_path)
    monkeypatch.setattr("al_mal_sync.unmapped.default_unmapped_state_path", lambda: unmapped_path)
    monkeypatch.setattr("al_mal_sync.config.default_sync_history_path", lambda: history_path)
    monkeypatch.setattr("al_mal_sync.sync_history.default_sync_history_path", lambda: history_path)
    return cfg


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, stats: DashboardStats) -> None:
    monkeypatch.setattr(dashboard_tab_module, "fetch_dashboard_stats", lambda cfg: stats)


class TestLastSyncAndNeedsAttention:
    def test_never_synced_shows_hint(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)

        assert "never synced" in tab.last_sync_label.text().lower()
        wait_until(qt_app, lambda: tab._thread is None)

    def test_shows_last_sync_summary(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_sync_history(
            SyncHistoryEntry(
                finished_at="2026-01-01T00:00:00+00:00",
                per_kind={"anime": {"updated": 3, "skipped": 1}},
            ),
            config.resolved_sync_history_path,
        )
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)

        tab = DashboardTab(lambda: config)

        assert "2026-01-01" in tab.last_sync_label.text()
        assert "3 updated" in tab.last_sync_label.text()
        wait_until(qt_app, lambda: tab._thread is None)

    def test_needs_attention_hidden_when_nothing_unmapped(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)

        assert tab.go_to_mapping_issues_button.isHidden() is True
        wait_until(qt_app, lambda: tab._thread is None)

    def test_needs_attention_shown_when_entries_unmapped(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_unmapped_state(
            UnmappedState(
                entries=[
                    UnmappedRecord(
                        title="X", anilist_id=1, mal_id=0, media_type="anime",
                        direction="forward", reason="r", updated_at="t",
                    )
                ]
            ),
            config.resolved_unmapped_state_path,
        )
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)

        tab = DashboardTab(lambda: config)

        assert tab.go_to_mapping_issues_button.isHidden() is False
        assert "1 entry" in tab.needs_attention_label.text()
        wait_until(qt_app, lambda: tab._thread is None)


class TestStatsWorker:
    def test_renders_authenticated_counts_and_diff(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=True, anime_count=10, manga_count=5),
            myanimelist=PlatformStatus(authenticated=True, anime_count=8, manga_count=5),
        )
        _stub_fetch(monkeypatch, stats)

        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: "AniList 10" in tab.anime_card.value_label.text())

        assert "AniList 10" in tab.anime_card.value_label.text()
        assert "MAL 8" in tab.anime_card.value_label.text()
        assert "AniList has 2 more" in tab.anime_card.subtext_label.text()
        assert "In sync" in tab.manga_card.subtext_label.text()
        assert "AniList: connected" in tab.anilist_status_label.text()

    def test_not_authenticated_shows_login_prompt_on_card(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=False),
            myanimelist=PlatformStatus(authenticated=True, anime_count=1, manga_count=1),
        )
        _stub_fetch(monkeypatch, stats)

        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab.anime_card.subtext_label.text() != "")

        assert "Log in to both accounts" in tab.anime_card.subtext_label.text()
        assert "AniList: not logged in" in tab.anilist_status_label.text()
        assert "MyAnimeList: connected" in tab.myanimelist_status_label.text()

    def test_error_on_one_platform_still_shows_the_other(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=True, error="boom"),
            myanimelist=PlatformStatus(authenticated=True, anime_count=1, manga_count=1),
        )
        _stub_fetch(monkeypatch, stats)

        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: "boom" in tab.anilist_status_label.text())

        assert "boom" in tab.anilist_status_label.text()
        assert "MyAnimeList: connected" in tab.myanimelist_status_label.text()


class TestRefreshThrottling:
    """AniList/MAL both rate-limit aggressively, and reload() fires on every
    nav switch to this page plus several cross-page events (see
    main_window.py) -- these guard against turning quick navigation into a
    burst of real API calls."""

    def test_reload_does_not_refetch_while_a_fetch_is_in_flight(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whether the worker thread has actually gotten around to calling
        # fetch_dashboard_stats yet is a separate race not worth coupling
        # this assertion to (see test_gui_sync_tab.py's identical note on
        # its own dedup-guard test) -- what matters is that a second reload()
        # doesn't start a *second* thread while one is already in flight.
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)  # first reload() already started a fetch thread
        first_thread = tab._thread

        tab.reload()

        assert tab._thread is first_thread
        wait_until(qt_app, lambda: tab._thread is None)

    def test_reload_skips_network_fetch_within_staleness_window(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def counting_fetch(cfg: Config) -> DashboardStats:
            calls.append(1)
            return _BOTH_LOGGED_OUT

        monkeypatch.setattr(dashboard_tab_module, "fetch_dashboard_stats", counting_fetch)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)
        assert len(calls) == 1

        tab.reload()  # well within the staleness window -- must not refetch

        assert len(calls) == 1

    def test_refresh_button_forces_a_fetch_even_within_the_staleness_window(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def counting_fetch(cfg: Config) -> DashboardStats:
            calls.append(1)
            return _BOTH_LOGGED_OUT

        monkeypatch.setattr(dashboard_tab_module, "fetch_dashboard_stats", counting_fetch)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)
        assert len(calls) == 1

        tab.refresh_button.click()
        wait_until(qt_app, lambda: len(calls) == 2)

        assert len(calls) == 2

    def test_local_reads_still_refresh_when_network_fetch_is_skipped(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)

        save_sync_history(
            SyncHistoryEntry(finished_at="2026-02-02T00:00:00+00:00", per_kind={}),
            config.resolved_sync_history_path,
        )

        tab.reload()  # network fetch skipped (staleness window), local reads must still run

        assert "2026-02-02" in tab.last_sync_label.text()


class TestNavigation:
    def test_go_to_sync_button_emits_navigate_requested(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)
        calls: list[str] = []
        tab.navigate_requested.connect(calls.append)

        tab.run_sync_button.click()

        assert calls == ["sync"]
        wait_until(qt_app, lambda: tab._thread is None)

    def test_go_to_login_button_emits_navigate_requested(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, _BOTH_LOGGED_OUT)
        tab = DashboardTab(lambda: config)
        calls: list[str] = []
        tab.navigate_requested.connect(calls.append)

        tab.go_to_login_button.click()

        assert calls == ["login"]
        wait_until(qt_app, lambda: tab._thread is None)
