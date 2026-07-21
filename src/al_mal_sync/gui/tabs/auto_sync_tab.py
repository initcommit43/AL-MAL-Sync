"""Auto-Sync page (formerly "Watch" -- renamed because that name told
casual users nothing about what it does): runs the Sync page's own sync on a
repeating schedule as a non-blocking QTimer loop, so the GUI stays
responsive. Reuses the Sync tab's own run button for the actual work each
tick -- same options, same progress bar/log panel/results view, no separate
copy of the sync-options form or worker-thread wiring.

The schedule itself (interval or cron) is configured on the Settings page
(config.watch.interval/schedule), not here -- having two editable schedule
fields in two places would just invite "which one actually wins" confusion.
This page only starts/stops the QTimer loop against whatever's configured.

The QTimer loop needs *some* process alive to host it, but that no longer
means the window itself has to stay open and in focus: main_window.py's
close button minimizes to a system tray icon instead of exiting whenever
`is_watching` is true, so starting this and closing the window is a real
"run this in the background" workflow, not just a same-session convenience.
Truly unattended scheduling with no GUI process running at all (e.g. on a
headless server) still needs the CLI's `watch` command or Docker -- that's
a different thing this page doesn't try to replace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from croniter import CroniterError, croniter
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from ...config import Config, ConfigError
from ..widgets import apply_page_layout, left_aligned
from .sync_tab import SyncTab

_BUTTON_WIDTH = 220

_IDLE_MESSAGE = (
    "Not running. Once started, you can close this window -- Auto-Sync keeps "
    "running in the background (look for the tray icon); quit from there to "
    "stop it. For unattended scheduling with no GUI running at all (e.g. a "
    "headless server), use `al-mal-sync watch` on the command line or Docker "
    "instead."
)


class AutoSyncTab(QWidget):
    def __init__(
        self,
        get_config: Callable[[], Config],
        sync_tab: SyncTab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._sync_tab = sync_tab
        self._next_run: datetime | None = None

        self._fire_timer = QTimer(self)
        self._fire_timer.setSingleShot(True)
        self._fire_timer.timeout.connect(self._on_timer_fired)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._update_status_label)

        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        title = QLabel("Auto-Sync", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Automatically runs a sync for you on a schedule. Start it, then close the "
            "window -- it keeps running in the background.",
            self,
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        group = QGroupBox("Schedule", self)
        group_layout = QVBoxLayout(group)

        self.schedule_label = QLabel(self)
        self.schedule_label.setWordWrap(True)
        self.schedule_label.setToolTip(
            "Set in Settings -> Watch Schedule. Either a plain interval (e.g. every 6\n"
            "hours) or, for advanced users, a cron expression."
        )
        group_layout.addWidget(self.schedule_label)

        self.toggle_button = QPushButton("Start Auto-Sync", self)
        self.toggle_button.setObjectName("primaryButton")
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        group_layout.addLayout(left_aligned(self.toggle_button, _BUTTON_WIDTH))

        self.status_label = QLabel(_IDLE_MESSAGE, self)
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        group_layout.addWidget(self.status_label)

        layout.addWidget(group)
        layout.addStretch(1)

        self.refresh_schedule_display()

    def refresh_schedule_display(self) -> None:
        """Called after Settings saves, so this page reflects the current
        config.watch value without needing its own copy of it."""
        watch = self._get_config().watch
        if watch.schedule:
            self.schedule_label.setText(f"Runs on a custom cron schedule ({watch.schedule!r}).")
        elif watch.interval:
            self.schedule_label.setText(f"Runs automatically every {watch.interval}.")
        else:
            self.schedule_label.setText(
                "No schedule set yet -- set how often to sync in the Settings page first."
            )

    @property
    def is_watching(self) -> bool:
        """Whether the QTimer loop is currently active -- read by
        main_window.py's closeEvent to decide whether closing the window
        should minimize to the tray instead of quitting."""
        return self._fire_timer.isActive() or self._countdown_timer.isActive()

    def _on_toggle_clicked(self) -> None:
        if self.is_watching:
            self._stop_watching()
        else:
            self._start_watching()

    def _start_watching(self) -> None:
        try:
            self._get_config().watch.validate()
        except ConfigError as exc:
            self.status_label.setText(f"Can't start: {exc}")
            return

        self.toggle_button.setText("Stop Auto-Sync")
        self._countdown_timer.start()
        self._schedule_next_run()

    def _stop_watching(self) -> None:
        self._fire_timer.stop()
        self._countdown_timer.stop()
        self._next_run = None
        self.toggle_button.setText("Start Auto-Sync")
        self.status_label.setText(_IDLE_MESSAGE)

    def _schedule_next_run(self) -> None:
        watch = self._get_config().watch
        if watch.schedule:
            try:
                cron = croniter(watch.schedule, datetime.now())
            except CroniterError as exc:
                self.status_label.setText(f"Invalid cron schedule: {exc}")
                self._stop_watching()
                return
            self._next_run = cron.get_next(datetime)
        else:
            interval = watch.get_interval()
            if interval is None:
                self.status_label.setText("No schedule set.")
                self._stop_watching()
                return
            self._next_run = datetime.now() + interval

        wait_ms = max(0, int((self._next_run - datetime.now()).total_seconds() * 1000))
        self._fire_timer.start(wait_ms)
        self._update_status_label()

    def _on_timer_fired(self) -> None:
        # If a previous auto-sync-triggered (or manually started) sync is
        # still running, SyncTab's own dedup guard silently skips this click
        # -- this tick is just missed, matching "don't overlap syncs" rather
        # than queuing up a backlog of runs.
        self._sync_tab.run_button.click()
        self._schedule_next_run()

    def _update_status_label(self) -> None:
        if self._next_run is None:
            return
        remaining = max(0, int((self._next_run - datetime.now()).total_seconds()))
        self.status_label.setText(
            f"Running. Next sync at {self._next_run.strftime('%H:%M:%S')} (in {remaining}s). "
            "You can close this window -- it'll keep running in the background."
        )
