"""Sync page: a simple "what to sync" + "direction" choice as the primary,
always-visible controls (with hover tooltips explaining each in plain
language), a big Run Sync button, and every other CLI flag (see cli.py's
_sync_options) tucked into a collapsed "Advanced options" section for anyone
who needs it. Runs sync.runner.run_sync() on a worker thread with a live
progress bar and log panel, and renders the resulting SyncStatistics/
SyncReport when done -- reusing the same structured objects and text
renderers the CLI uses, not a hand-rolled summary.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from ..widgets import CollapsibleSection, apply_page_layout, cap_width, left_aligned
from ..workers import run_in_thread

_FIELD_WIDTH = 340
_BUTTON_WIDTH = 220

_WHAT_ITEMS = ("Anime", "Manga", "Both anime and manga")
_DIRECTION_ITEMS = ("AniList -> MyAnimeList (recommended)", "MyAnimeList -> AniList")


class SyncTab(QWidget):
    # Worker-thread callbacks (on_progress/on_kind_start passed into
    # run_sync) only ever call .emit() on these -- Signal emission is
    # thread-safe by design. The actual widget updates happen in the
    # connected slots below, which Qt marshals onto the GUI thread because
    # this SyncTab (the signals' owner) lives there. See gui/workers.py's
    # docstring for why this indirection is required.
    progress_updated = Signal(int, int)
    kind_started = Signal(str, bool)
    # Emitted once a run (successful or not) finishes, so the Dashboard can
    # refresh its last-sync card without polling.
    sync_finished = Signal()

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
        apply_page_layout(layout)
        title = QLabel("Sync", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("Choose what to sync, then run it. Hover any option for details.", self)
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_primary_options_group())

        advanced_content = self._build_advanced_options_group()
        layout.addWidget(CollapsibleSection("Advanced options", advanced_content, collapsed=True))

        self.run_button = QPushButton("Run Sync Now", self)
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self._on_run_clicked)
        layout.addLayout(left_aligned(self.run_button, _BUTTON_WIDTH))

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1)
        layout.addWidget(self.progress_bar)

        self.kind_label = QLabel("", self)
        self.kind_label.setObjectName("muted")
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

    def _build_primary_options_group(self) -> QGroupBox:
        group = QGroupBox("What to sync", self)
        form = QFormLayout(group)

        self.what_to_sync_combo = QComboBox(group)
        self.what_to_sync_combo.addItems(_WHAT_ITEMS)
        self.what_to_sync_combo.setToolTip(
            "Choose whether to sync anime, manga, or both.\nMost people start with anime only."
        )
        form.addRow("Content", cap_width(self.what_to_sync_combo, _FIELD_WIDTH))

        self.direction_combo = QComboBox(group)
        self.direction_combo.addItems(_DIRECTION_ITEMS)
        self.direction_combo.setToolTip(
            "Which app's list is treated as the source of truth.\n"
            "The recommended direction copies your AniList changes to MyAnimeList.\n"
            "The reverse direction copies MyAnimeList changes to AniList instead."
        )
        form.addRow("Direction", cap_width(self.direction_combo, _FIELD_WIDTH))

        return group

    def _build_advanced_options_group(self) -> QWidget:
        # A plain widget, not a QGroupBox -- the CollapsibleSection wrapping
        # this already supplies the "Advanced options" heading and boundary,
        # so a second bordered/titled box nested directly inside it would
        # just duplicate that framing.
        group = QWidget(self)
        form = QFormLayout(group)
        form.setContentsMargins(4, 8, 4, 4)

        self.force_checkbox = QCheckBox("Force (skip matching, sync by ID directly)", group)
        self.force_checkbox.setToolTip(
            "Skip automatic title matching and update entries by ID directly.\n"
            "Only useful for troubleshooting -- leave this off normally."
        )
        self.dry_run_checkbox = QCheckBox("Dry run (preview only, no changes made)", group)
        self.dry_run_checkbox.setToolTip(
            "Show what would change without actually updating anything.\n"
            "A good way to see what a sync will do before running it for real."
        )
        self.offline_db_checkbox = QCheckBox("Force-enable offline database", group)
        self.offline_db_checkbox.setToolTip(
            "Use a downloaded anime database to help match titles between AniList\n"
            "and MyAnimeList. Recommended -- faster and works without extra API calls."
        )
        self.offline_db_refresh_checkbox = QCheckBox("Force-refresh offline database cache", group)
        self.offline_db_refresh_checkbox.setToolTip(
            "Re-download the offline anime database before this sync, even if the\n"
            "cached copy is still fresh. Rarely needed."
        )
        self.arm_api_checkbox = QCheckBox("Enable ARM API fallback", group)
        self.arm_api_checkbox.setToolTip(
            "Use an extra online lookup service as a fallback when other methods\n"
            "can't match a title. Optional."
        )
        self.arm_api_url_field = QLineEdit(group)
        self.arm_api_url_field.setPlaceholderText("override ARM API base URL (optional)")
        self.arm_api_url_field.setToolTip("Only needed if you run your own copy of the ARM service.")
        self.jikan_api_checkbox = QCheckBox("Enable Jikan API", group)
        self.jikan_api_checkbox.setToolTip(
            "Use the Jikan API (unofficial MyAnimeList data) to help match manga\n"
            "titles, and to sync favorites. Optional."
        )
        self.favorites_checkbox = QCheckBox("Also sync favorites", group)
        self.favorites_checkbox.setToolTip(
            "Keep your favorited anime/manga in sync between AniList and\n"
            "MyAnimeList. Requires the Jikan API, which will be turned on automatically."
        )

        for box in (
            self.force_checkbox, self.dry_run_checkbox,
            self.offline_db_checkbox, self.offline_db_refresh_checkbox,
            self.arm_api_checkbox,
        ):
            form.addRow(box)
        form.addRow("ARM API URL", cap_width(self.arm_api_url_field, _FIELD_WIDTH))
        form.addRow(self.jikan_api_checkbox)
        form.addRow(self.favorites_checkbox)
        return group

    # -- run ---------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._sync_thread is not None:
            return  # a sync is already running
        config = self._get_config()

        manga = self.what_to_sync_combo.currentIndex() == 1
        all_media = self.what_to_sync_combo.currentIndex() == 2
        reverse = self.direction_combo.currentIndex() == 1

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
            manga=manga,
            all_media=all_media,
            reverse=reverse,
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
        self.sync_finished.emit()

    def _on_sync_error(self, message: str) -> None:
        self._sync_thread = None
        self._sync_worker = None
        self.run_button.setEnabled(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.kind_label.setText("Sync failed.")
        self.results_view.setPlainText(f"Error: {message}")
        self.sync_finished.emit()
