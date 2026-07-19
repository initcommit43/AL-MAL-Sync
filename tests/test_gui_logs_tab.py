"""Tests for gui/tabs/logs_tab.py: log lines from the shared QtLogHandler
reach the log view, the verbose toggle changes the al_mal_sync logger's
level, and Clear empties the view without touching the logger."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.gui import log_bridge  # noqa: E402
from al_mal_sync.gui.tabs.logs_tab import LogsTab  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def log_handler() -> log_bridge.QtLogHandler:
    return log_bridge.QtLogHandler()


@pytest.fixture(autouse=True)
def _reset_logger_level() -> None:
    logger = logging.getLogger(log_bridge.PACKAGE_LOGGER_NAME)
    original = logger.level
    yield
    logger.setLevel(original)


class TestLogsTab:
    def test_log_line_is_appended(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = LogsTab(log_handler)

        log_handler.log_emitted.emit("something happened", logging.INFO)

        assert "something happened" in tab.log_view.toPlainText()

    def test_multiple_lines_accumulate(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = LogsTab(log_handler)

        log_handler.log_emitted.emit("first", logging.INFO)
        log_handler.log_emitted.emit("second", logging.INFO)

        text = tab.log_view.toPlainText()
        assert "first" in text
        assert "second" in text

    def test_clear_button_empties_view(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = LogsTab(log_handler)
        log_handler.log_emitted.emit("something", logging.INFO)

        tab.clear_button.click()

        assert tab.log_view.toPlainText() == ""

    def test_verbose_checkbox_sets_debug_level(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = LogsTab(log_handler)
        logging.getLogger(log_bridge.PACKAGE_LOGGER_NAME).setLevel(logging.INFO)

        tab.verbose_checkbox.setChecked(True)

        assert logging.getLogger(log_bridge.PACKAGE_LOGGER_NAME).level == logging.DEBUG

    def test_unchecking_verbose_restores_info_level(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = LogsTab(log_handler)
        tab.verbose_checkbox.setChecked(True)

        tab.verbose_checkbox.setChecked(False)

        assert logging.getLogger(log_bridge.PACKAGE_LOGGER_NAME).level == logging.INFO
