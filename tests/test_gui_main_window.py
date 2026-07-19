"""Tests for gui/main_window.py: all tabs wired up, and the Help menu
survives construction -- a regression test for a real crash found during
development (QMenu/QAction objects with no Python-side reference had their
underlying C++ objects deleted despite being parented to the menu bar; see
main_window.py's _build_menu comment). config_path is always redirected to
tmp_path so this never touches the user's real config.yaml/token store."""

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
    def test_all_seven_tabs_present(self, window: MainWindow) -> None:
        titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        assert titles == [
            "Settings", "Login", "Sync", "Watch", "Unmapped", "Mappings", "Logs",
        ]

    def test_help_menu_survives_construction(self, window: MainWindow) -> None:
        # Regression test: this used to raise
        # "RuntimeError: libshiboken: Internal C++ object (QMenu) already
        # deleted" because the menu/actions were local variables in
        # _build_menu() with no surviving Python reference.
        action_texts = [action.text() for action in window.menuBar().actions()]
        assert action_texts == ["Help"]

        help_action_texts = [action.text() for action in window.help_menu.actions()]
        assert help_action_texts == ["Open Config Folder", "About"]

    def test_open_config_folder_action_calls_qdesktopservices(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "al_mal_sync.gui.main_window.QDesktopServices.openUrl",
            lambda url: calls.append(url.toLocalFile()),
        )

        window.open_config_action.trigger()

        assert len(calls) == 1

    def test_about_action_shows_dialog(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "al_mal_sync.gui.main_window.QMessageBox.about",
            lambda parent, title, text: calls.append((title, text)),
        )

        window.about_action.trigger()

        assert len(calls) == 1
        assert calls[0][0] == "About AL-MAL-Sync"

    def test_switching_to_unmapped_tab_triggers_reload(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(window.unmapped_tab, "reload", lambda: calls.append(1))

        index = window.tabs.indexOf(window.unmapped_tab)
        window.tabs.setCurrentIndex(index)

        assert calls == [1]
