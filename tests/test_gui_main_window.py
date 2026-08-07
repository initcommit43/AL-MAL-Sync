"""Tests for gui/main_window.py: all six pages wired up in the
dashboard-first/settings-last sidebar order (Logs and the Help menu both
moved elsewhere -- see gui/tabs/sync_tab.py and gui/tabs/settings_tab.py).
config_path is always redirected to tmp_path so this never touches the
user's real config.yaml/token store."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.gui import main_window as main_window_module  # noqa: E402
from al_mal_sync.gui.main_window import MainWindow  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def window(qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    monkeypatch.setattr(
        main_window_module, "default_config_path", lambda: str(tmp_path / "config.yaml")
    )
    return MainWindow()


class TestMainWindow:
    def test_all_six_pages_present_in_dashboard_first_settings_last_order(
        self, window: MainWindow
    ) -> None:
        titles = [window.nav_list.item(i).text() for i in range(window.nav_list.count())]
        assert titles == [
            "Dashboard", "Auto-Sync", "Manual Sync", "Login", "Mapping Issues", "Settings",
        ]

    def test_dashboard_is_the_page_shown_on_startup(self, window: MainWindow) -> None:
        assert window.nav_list.currentRow() == 0
        assert window.stack.currentWidget() is window.dashboard_tab

    def test_switching_to_mapping_issues_page_triggers_reload(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(window.mapping_issues_tab, "reload", lambda: calls.append(1))

        index = window.stack.indexOf(window.mapping_issues_tab)
        window.nav_list.setCurrentRow(index)

        assert calls == [1]

    def test_dashboard_navigate_requested_switches_page(self, window: MainWindow) -> None:
        window.dashboard_tab.navigate_requested.emit("login")

        assert window.nav_list.currentRow() == window.stack.indexOf(window.login_tab)
        assert window.stack.currentWidget() is window.login_tab

    def test_login_auth_changed_refreshes_dashboard(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(window.dashboard_tab, "reload", lambda: calls.append(1))

        window.login_tab.auth_changed.emit()

        assert calls == [1]

    def test_sync_finished_refreshes_dashboard(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(window.dashboard_tab, "reload", lambda: calls.append(1))

        window.sync_tab.sync_finished.emit()

        assert calls == [1]


class TestPartialConfigSurvivesRestart:
    def test_saved_username_is_not_discarded_on_next_launch(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression test for a real bug: _load_initial_config used to call
        load_config() with its default strict validate=True, which raises --
        discarding the whole config back to blank -- whenever any *other*
        required field (e.g. client_id) was still empty, even though the
        username itself had been saved correctly (e.g. by the Login page's
        "Fetch my username" button). A restart should never erase progress
        a user (or the app, on their behalf) already saved."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("anilist:\n  username: saved_user\n", encoding="utf-8")
        monkeypatch.setattr(main_window_module, "default_config_path", lambda: str(config_path))

        window = MainWindow()

        assert window.config.anilist.username == "saved_user"
