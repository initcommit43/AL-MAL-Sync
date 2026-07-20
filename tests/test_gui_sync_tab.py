"""Tests for gui/tabs/sync_tab.py: button state while a sync is in flight,
progress-bar/log updates arriving safely on the GUI thread from the worker
thread, and rendering of the finished/error outcomes. run_sync itself is
monkeypatched at the sync_tab module level -- these tests exercise the
tab's own wiring, not the real sync pipeline (that's sync/runner.py's and
Updater's job, covered elsewhere)."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui import log_bridge  # noqa: E402
from al_mal_sync.gui.tabs import sync_tab as sync_tab_module  # noqa: E402
from al_mal_sync.gui.tabs.sync_tab import SyncTab  # noqa: E402
from al_mal_sync.sync.updater import SyncOutcome  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def log_handler() -> log_bridge.QtLogHandler:
    return log_bridge.QtLogHandler()


class TestSyncTab:
    def test_run_button_disabled_while_sync_in_flight(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        release = threading.Event()

        def blocking_run_sync(config: Config, **kwargs: object) -> tuple[dict, dict]:
            release.wait(timeout=5)
            return {}, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", blocking_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        try:
            tab.run_button.click()
            assert tab.run_button.isEnabled() is False
        finally:
            release.set()
            wait_until(qt_app, lambda: tab._sync_thread is None)

    def test_second_click_while_running_is_ignored(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        release = threading.Event()

        def blocking_run_sync(config: Config, **kwargs: object) -> tuple[dict, dict]:
            release.wait(timeout=5)
            return {}, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", blocking_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        try:
            tab.run_button.click()
            first_thread = tab._sync_thread
            tab.run_button.click()

            # The dedup guard in _on_run_clicked is what matters here, and
            # it's checkable synchronously (no new thread started); whether
            # the background thread has actually gotten around to calling
            # blocking_run_sync yet is a separate race not worth coupling
            # this assertion to.
            assert tab._sync_thread is first_thread
        finally:
            release.set()
            wait_until(qt_app, lambda: tab._sync_thread is None)

    def test_progress_updates_reach_progress_bar_from_worker_thread(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Blocks after emitting progress so run_sync can't also emit
        # `finished` before the test observes the intermediate state --
        # otherwise wait_until's settle phase (needed to avoid leaking
        # cleanup into the next test, see conftest.py) can race ahead and
        # process completion too, since a non-blocking fake returns almost
        # instantly on the worker thread.
        release = threading.Event()

        def fake_run_sync(config: Config, *, on_progress=None, on_kind_start=None, **kwargs: object):
            if on_kind_start is not None:
                on_kind_start("anime", False)
            if on_progress is not None:
                on_progress(3, 10)
            release.wait(timeout=5)
            return {}, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", fake_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        try:
            tab.run_button.click()
            wait_until(qt_app, lambda: tab.progress_bar.value() == 3)

            assert tab.progress_bar.maximum() == 10
            assert tab.progress_bar.value() == 3
            assert "anime" in tab.kind_label.text()
        finally:
            release.set()
            wait_until(qt_app, lambda: tab._sync_thread is None)

    def test_on_finished_renders_statistics_and_report(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outcomes = {"anime": SyncOutcome()}

        def fake_run_sync(config: Config, **kwargs: object):
            return outcomes, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", fake_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert tab.run_button.isEnabled() is True
        assert tab.results_view.toPlainText() != ""
        assert tab._sync_thread is None

    def test_on_error_shows_message_and_re_enables_button(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing_run_sync(config: Config, **kwargs: object):
            raise RuntimeError("boom")

        monkeypatch.setattr(sync_tab_module, "run_sync", failing_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert "boom" in tab.results_view.toPlainText()
        assert tab._sync_thread is None

    def test_log_line_from_handler_is_appended(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler
    ) -> None:
        tab = SyncTab(lambda: Config(), log_handler)

        log_handler.log_emitted.emit("hello from updater", 20)

        assert "hello from updater" in tab.log_view.toPlainText()

    def test_what_to_sync_and_direction_selection_maps_to_run_sync_kwargs(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_sync(config: Config, **kwargs: object):
            captured.update(kwargs)
            return {}, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", fake_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        tab.what_to_sync_combo.setCurrentIndex(2)  # "Both anime and manga"
        tab.direction_combo.setCurrentIndex(1)  # reverse
        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert captured["manga"] is False
        assert captured["all_media"] is True
        assert captured["reverse"] is True

    def test_manga_only_selection_maps_to_manga_kwarg(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_sync(config: Config, **kwargs: object):
            captured.update(kwargs)
            return {}, {}

        monkeypatch.setattr(sync_tab_module, "run_sync", fake_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)

        tab.what_to_sync_combo.setCurrentIndex(1)  # "Manga"
        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert captured["manga"] is True
        assert captured["all_media"] is False
        assert captured["reverse"] is False

    def test_sync_finished_signal_emitted_on_success(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sync_tab_module, "run_sync", lambda config, **kwargs: ({}, {}))
        tab = SyncTab(lambda: Config(), log_handler)
        calls = []
        tab.sync_finished.connect(lambda: calls.append(1))

        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert calls == [1]

    def test_sync_finished_signal_emitted_on_error(
        self, qt_app: QApplication, log_handler: log_bridge.QtLogHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing_run_sync(config: Config, **kwargs: object):
            raise RuntimeError("boom")

        monkeypatch.setattr(sync_tab_module, "run_sync", failing_run_sync)
        tab = SyncTab(lambda: Config(), log_handler)
        calls = []
        tab.sync_finished.connect(lambda: calls.append(1))

        tab.run_button.click()
        wait_until(qt_app, lambda: tab.run_button.isEnabled())

        assert calls == [1]
