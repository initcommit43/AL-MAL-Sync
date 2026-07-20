"""Tests for gui/tabs/auto_sync_tab.py: schedule display mirroring
config.watch, start/stop validation, and that a fired timer triggers the
Sync tab's run button (not a duplicate copy of run_sync wiring). A
lightweight fake stands in for SyncTab -- AutoSyncTab only ever touches
`sync_tab.run_button`."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from al_mal_sync.config import Config, WatchConfig  # noqa: E402
from al_mal_sync.gui.tabs.auto_sync_tab import AutoSyncTab  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.


class _FakeSyncTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.run_button = QPushButton(self)
        self.click_count = 0
        self.run_button.clicked.connect(self._on_click)

    def _on_click(self) -> None:
        self.click_count += 1


class TestAutoSyncTab:
    def test_no_schedule_shows_hint(self, qt_app: QApplication) -> None:
        tab = AutoSyncTab(lambda: Config(), _FakeSyncTab())

        assert "No schedule set" in tab.schedule_label.text()
        assert tab.toggle_button.text() == "Start Auto-Sync"

    def test_shows_configured_interval(self, qt_app: QApplication) -> None:
        cfg = Config()
        cfg.watch.interval = "6h"
        tab = AutoSyncTab(lambda: cfg, _FakeSyncTab())

        assert "6h" in tab.schedule_label.text()

    def test_shows_configured_cron_schedule(self, qt_app: QApplication) -> None:
        cfg = Config()
        cfg.watch.schedule = "0 */6 * * *"
        tab = AutoSyncTab(lambda: cfg, _FakeSyncTab())

        assert "0 */6 * * *" in tab.schedule_label.text()

    def test_start_with_no_schedule_shows_error_and_does_not_start(self, qt_app: QApplication) -> None:
        tab = AutoSyncTab(lambda: Config(), _FakeSyncTab())

        tab.toggle_button.click()

        assert "Can't start" in tab.status_label.text()
        assert tab.toggle_button.text() == "Start Auto-Sync"
        assert tab._is_watching() is False

    def test_start_with_valid_interval_toggles_button_and_shows_countdown(
        self, qt_app: QApplication
    ) -> None:
        cfg = Config()
        cfg.watch.interval = "1h"
        tab = AutoSyncTab(lambda: cfg, _FakeSyncTab())

        try:
            tab.toggle_button.click()

            assert tab.toggle_button.text() == "Stop Auto-Sync"
            assert tab._is_watching() is True
            assert "Next sync at" in tab.status_label.text()
        finally:
            tab._stop_watching()

    def test_stop_resets_button_and_status(self, qt_app: QApplication) -> None:
        cfg = Config()
        cfg.watch.interval = "1h"
        tab = AutoSyncTab(lambda: cfg, _FakeSyncTab())
        tab.toggle_button.click()

        tab.toggle_button.click()  # second click while watching = stop

        assert tab.toggle_button.text() == "Start Auto-Sync"
        assert tab._is_watching() is False
        assert "Not running automatically" in tab.status_label.text()

    def test_timer_fire_clicks_sync_tab_run_button_and_reschedules(
        self, qt_app: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bypass the CLI-parity 1h-168h range check so this test doesn't
        # need to wait a real hour -- only the QTimer scheduling/reschedule
        # behavior is under test here, not WatchConfig's validation rules
        # (those are covered in test_config.py).
        monkeypatch.setattr(WatchConfig, "validate", lambda self: None)
        cfg = Config()
        cfg.watch.interval = "1s"
        fake_sync_tab = _FakeSyncTab()
        tab = AutoSyncTab(lambda: cfg, fake_sync_tab)

        try:
            tab.toggle_button.click()
            assert wait_until(qt_app, lambda: fake_sync_tab.click_count >= 1, timeout_ms=3000)
            # Rescheduled for another tick, not left stopped after firing once.
            assert tab._is_watching() is True
        finally:
            tab._stop_watching()

    def test_refresh_schedule_display_reflects_config_changes(self, qt_app: QApplication) -> None:
        cfg = Config()
        tab = AutoSyncTab(lambda: cfg, _FakeSyncTab())
        assert "No schedule set" in tab.schedule_label.text()

        cfg.watch.interval = "12h"
        tab.refresh_schedule_display()

        assert "12h" in tab.schedule_label.text()
