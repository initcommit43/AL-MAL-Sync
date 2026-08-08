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
from al_mal_sync.stats import LibraryStats, StatusCounts  # noqa: E402
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
    def test_renders_authenticated_counts_per_platform(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=True, anime_count=10, manga_count=5),
            myanimelist=PlatformStatus(authenticated=True, anime_count=8, manga_count=5),
        )
        _stub_fetch(monkeypatch, stats)

        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab.anilist_card._value_labels["Anime"].text() == "10")

        assert tab.anilist_card._value_labels["Anime"].text() == "10"
        assert tab.anilist_card._value_labels["Manga"].text() == "5"
        assert tab.mal_card._value_labels["Anime"].text() == "8"
        assert tab.mal_card._value_labels["Manga"].text() == "5"
        assert tab.anilist_card.subtext_label.isVisible() is False
        assert "AniList: connected" in tab.anilist_status_label.text()

    def test_not_authenticated_shows_login_prompt_on_that_platforms_card(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=False),
            myanimelist=PlatformStatus(authenticated=True, anime_count=1, manga_count=1),
        )
        _stub_fetch(monkeypatch, stats)

        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab.anilist_card.subtext_label.text() != "")

        assert "Log in to see this" in tab.anilist_card.subtext_label.text()
        assert tab.anilist_card._value_labels["Anime"].text() == "--"
        assert tab.mal_card.subtext_label.isVisible() is False
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
        assert "boom" in tab.anilist_card.subtext_label.text()
        assert tab.mal_card._value_labels["Anime"].text() == "1"
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


class TestLibraryStats:
    """The stats-source selector renders from whatever DashboardStats the
    last fetch produced -- switching it never triggers a new fetch (both
    platforms' LibraryStats already came back together), and the
    AniList-only Days Watched card is hidden outright under MyAnimeList
    since MAL's data can't support it (see stats.py)."""

    _ANILIST_STATS = LibraryStats(
        anime_status=StatusCounts(current=1, completed=2, planning=3, paused=4, dropped=5),
        manga_status=StatusCounts(current=6, completed=7, planning=8, paused=9, dropped=10),
        anime_episodes_watched=100,
        manga_chapters_read=200,
        manga_volumes_read=20,
        anime_mean_score=8.5,
        manga_mean_score=7.25,
        anime_days_watched=12.3,
    )
    _MAL_STATS = LibraryStats(
        anime_status=StatusCounts(current=11, completed=12, planning=13, paused=14, dropped=15),
        manga_status=StatusCounts(current=16, completed=17, planning=18, paused=19, dropped=20),
        anime_episodes_watched=50,
        manga_chapters_read=60,
        manga_volumes_read=6,
        anime_mean_score=6.0,
        manga_mean_score=None,
        anime_days_watched=None,  # MAL can never supply this
    )
    _BOTH_WITH_STATS = DashboardStats(
        anilist=PlatformStatus(authenticated=True, anime_count=15, manga_count=40, stats=_ANILIST_STATS),
        myanimelist=PlatformStatus(
            authenticated=True, anime_count=65, manga_count=76, stats=_MAL_STATS
        ),
    )

    def test_anilist_selected_by_default_shows_its_stats_and_days_watched_card(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_fetch(monkeypatch, self._BOTH_WITH_STATS)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)

        assert tab.stats_source_combo.currentData() == "anilist"
        assert tab.days_watched_card.isHidden() is False
        assert tab.days_watched_card._value_labels["Anime"].text() == "12.3"
        assert tab.status_anime_card._value_labels["Watching"].text() == "1"
        assert tab.scores_card._value_labels["Anime"].text() == "8.50"
        assert tab.progress_card._value_labels["Episodes watched"].text() == "100"

    def test_switching_to_myanimelist_hides_days_watched_and_rerenders_without_refetching(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def counting_fetch(cfg: Config) -> DashboardStats:
            calls.append(1)
            return self._BOTH_WITH_STATS

        monkeypatch.setattr(dashboard_tab_module, "fetch_dashboard_stats", counting_fetch)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)
        assert len(calls) == 1

        tab.stats_source_combo.setCurrentIndex(
            tab.stats_source_combo.findData("myanimelist")
        )

        assert len(calls) == 1  # switching sources must not trigger a network call
        assert tab.days_watched_card.isHidden() is True
        assert tab.status_anime_card._value_labels["Watching"].text() == "11"
        assert tab.scores_card._value_labels["Anime"].text() == "6.00"
        assert tab.scores_card._value_labels["Manga"].text() == "--"
        assert tab.progress_card._value_labels["Episodes watched"].text() == "50"

    def test_not_authenticated_source_clears_cards_with_login_prompt(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats = DashboardStats(
            anilist=PlatformStatus(authenticated=False),
            myanimelist=PlatformStatus(authenticated=True, anime_count=1, manga_count=1, stats=self._MAL_STATS),
        )
        _stub_fetch(monkeypatch, stats)
        tab = DashboardTab(lambda: config)
        wait_until(qt_app, lambda: tab._thread is None)

        assert tab.status_anime_card._value_labels["Watching"].text() == "--"
        assert "Log in to see this" in tab.status_anime_card.subtext_label.text()


class TestNavigation:
    def test_review_button_emits_navigate_requested(
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
        calls: list[str] = []
        tab.navigate_requested.connect(calls.append)

        tab.go_to_mapping_issues_button.click()

        assert calls == ["mapping_issues"]
        wait_until(qt_app, lambda: tab._thread is None)
