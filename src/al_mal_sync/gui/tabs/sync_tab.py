"""Sync tab: mirrors the CLI `sync` command's flags (see cli.py's
_sync_options) as checkboxes, runs sync.runner.run_sync() on a worker
thread with a live progress bar and log panel, and renders the resulting
SyncStatistics/SyncReport when done -- reusing the same structured objects
and text renderers the CLI uses, not a hand-rolled summary.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...sync.report import build_report, format_report
from ...sync.runner import run_sync
from ...sync.statistics import SyncStatistics, format_statistics_table
from ..log_bridge import QtLogHandler
from ..workers import run_in_thread


class SyncTab(QWidget):
    # Worker-thread callbacks (on_progress/on_kind_start passed into
    # run_sync) only ever call .emit() on these -- Signal emission is
    # thread-safe by design. The actual widget updates happen in the
    # connected slots below, which Qt marshals onto the GUI thread because
    # this SyncTab (the signals' owner) lives there. See gui/workers.py's
    # docstring for why this indirection is required.
    progress_updated = Signal(int, int)
    kind_started = Signal(str, bool)

    def __init__(
        self,
        get_config: Callable[[], Config],
        log_handler: QtLogHandler,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._sync_thread = None
        self._sync_worker = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_options_group())

        self.run_button = QPushButton("Run Sync Now", self)
        self.run_button.clicked.connect(self._on_run_clicked)
        layout.addWidget(self.run_button)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1)
        layout.addWidget(self.progress_bar)

        self.kind_label = QLabel("", self)
        layout.addWidget(self.kind_label)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        self.results_view = QPlainTextEdit(self)
        self.results_view.setReadOnly(True)
        layout.addWidget(self.results_view, 1)

        self.progress_updated.connect(self._update_progress_bar)
        self.kind_started.connect(self._update_kind_label)
        log_handler.log_emitted.connect(self._append_log_line)

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox("Sync Options", self)
        form = QFormLayout(group)

        self.manga_checkbox = QCheckBox("Manga instead of anime", group)
        self.all_checkbox = QCheckBox("Both anime and manga", group)
        self.reverse_checkbox = QCheckBox("Reverse direction (MyAnimeList -> AniList)", group)
        self.force_checkbox = QCheckBox("Force (skip matching, sync by ID directly)", group)
        self.dry_run_checkbox = QCheckBox("Dry run (preview only, no writes)", group)
        self.offline_db_checkbox = QCheckBox("Force-enable offline database", group)
        self.offline_db_refresh_checkbox = QCheckBox("Force-refresh offline database cache", group)
        self.arm_api_checkbox = QCheckBox("Enable ARM API fallback", group)
        self.arm_api_url_field = QLineEdit(group)
        self.arm_api_url_field.setPlaceholderText("override ARM API base URL (optional)")
        self.jikan_api_checkbox = QCheckBox("Enable Jikan API", group)
        self.favorites_checkbox = QCheckBox("Also sync favorites", group)

        for box in (
            self.manga_checkbox, self.all_checkbox, self.reverse_checkbox,
            self.force_checkbox, self.dry_run_checkbox,
            self.offline_db_checkbox, self.offline_db_refresh_checkbox,
            self.arm_api_checkbox,
        ):
            form.addRow(box)
        form.addRow("ARM API URL", self.arm_api_url_field)
        form.addRow(self.jikan_api_checkbox)
        form.addRow(self.favorites_checkbox)
        return group

    # -- run ---------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._sync_thread is not None:
            return  # a sync is already running
        config = self._get_config()

        self.run_button.setEnabled(False)
        self.progress_bar.setRange(0, 0)  # indeterminate until the first on_progress call
        self.kind_label.setText("Starting...")
        self.log_view.clear()
        self.results_view.clear()

        self._sync_thread, self._sync_worker = run_in_thread(
            self,
            run_sync,
            config,
            force=self.force_checkbox.isChecked(),
            dry_run=self.dry_run_checkbox.isChecked(),
            manga=self.manga_checkbox.isChecked(),
            all_media=self.all_checkbox.isChecked(),
            reverse=self.reverse_checkbox.isChecked(),
            offline_db=self.offline_db_checkbox.isChecked(),
            offline_db_force_refresh=self.offline_db_refresh_checkbox.isChecked(),
            arm_api=self.arm_api_checkbox.isChecked(),
            arm_api_url=self.arm_api_url_field.text().strip() or None,
            jikan_api=self.jikan_api_checkbox.isChecked(),
            favorites=self.favorites_checkbox.isChecked(),
            on_kind_start=self._emit_kind_started,
            on_progress=self._emit_progress,
            on_finished=self._on_sync_finished,
            on_error=self._on_sync_error,
        )

    def _emit_progress(self, current: int, total: int) -> None:
        self.progress_updated.emit(current, total)

    def _emit_kind_started(self, kind: str, reverse: bool) -> None:
        self.kind_started.emit(kind, reverse)

    def _update_progress_bar(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)

    def _update_kind_label(self, kind: str, reverse: bool) -> None:
        direction = "MyAnimeList -> AniList" if reverse else "AniList -> MyAnimeList"
        self.kind_label.setText(f"Syncing {kind} ({direction})...")

    def _append_log_line(self, message: str, _levelno: int) -> None:
        self.log_view.appendPlainText(message)

    def _on_sync_finished(self, result: object) -> None:
        outcomes, favorites_outcomes = result
        self._sync_thread = None
        self._sync_worker = None
        self.run_button.setEnabled(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.kind_label.setText("Done.")
        stats_text = format_statistics_table(SyncStatistics.from_outcomes(outcomes))
        report_text = format_report(build_report(outcomes, favorites_outcomes))
        self.results_view.setPlainText(f"{stats_text}\n\n{report_text}")

    def _on_sync_error(self, message: str) -> None:
        self._sync_thread = None
        self._sync_worker = None
        self.run_button.setEnabled(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.kind_label.setText("Sync failed.")
        self.results_view.setPlainText(f"Error: {message}")
