"""Watch tab: mirrors the CLI `watch` command's interval/cron scheduling,
but as a non-blocking QTimer loop instead of a blocking sleep loop, so the
GUI stays responsive. Reuses the Sync tab's own run button for the actual
work each tick -- same options, same progress bar/log panel/results view,
no separate copy of the sync-options form or worker-thread wiring.

The schedule itself (interval or cron) is configured on the Settings tab
(config.watch.interval/schedule), not here -- having two editable schedule
fields in two tabs would just invite "which one actually wins" confusion.
This tab only starts/stops the QTimer loop against whatever's configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from croniter import CroniterError, croniter
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ...config import Config, ConfigError
from .sync_tab import SyncTab

_IDLE_MESSAGE = (
    "Not watching. Runs only while this window is open -- for unattended "
    "background scheduling, use `al-mal-sync watch` on the command line or Docker."
)


class WatchTab(QWidget):
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

        self.schedule_label = QLabel(self)
        self.schedule_label.setWordWrap(True)
        layout.addWidget(self.schedule_label)

        self.toggle_button = QPushButton("Start Watching", self)
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        layout.addWidget(self.toggle_button)

        self.status_label = QLabel(_IDLE_MESSAGE, self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.refresh_schedule_display()

    def refresh_schedule_display(self) -> None:
        """Called after Settings saves, so this tab reflects the current
        config.watch value without needing its own copy of it."""
        watch = self._get_config().watch
        if watch.schedule:
            self.schedule_label.setText(f"Schedule: cron {watch.schedule!r} (set in Settings)")
        elif watch.interval:
            self.schedule_label.setText(f"Schedule: every {watch.interval} (set in Settings)")
        else:
            self.schedule_label.setText(
                "No schedule configured -- set a watch interval or cron schedule "
                "in the Settings tab first."
            )

    def _is_watching(self) -> bool:
        return self._fire_timer.isActive() or self._countdown_timer.isActive()

    def _on_toggle_clicked(self) -> None:
        if self._is_watching():
            self._stop_watching()
        else:
            self._start_watching()

    def _start_watching(self) -> None:
        try:
            self._get_config().watch.validate()
        except ConfigError as exc:
            self.status_label.setText(f"Cannot start: {exc}")
            return

        self.toggle_button.setText("Stop Watching")
        self._countdown_timer.start()
        self._schedule_next_run()

    def _stop_watching(self) -> None:
        self._fire_timer.stop()
        self._countdown_timer.stop()
        self._next_run = None
        self.toggle_button.setText("Start Watching")
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
                self.status_label.setText("No schedule configured.")
                self._stop_watching()
                return
            self._next_run = datetime.now() + interval

        wait_ms = max(0, int((self._next_run - datetime.now()).total_seconds() * 1000))
        self._fire_timer.start(wait_ms)
        self._update_status_label()

    def _on_timer_fired(self) -> None:
        # If a previous watch-triggered (or manually started) sync is still
        # running, SyncTab's own dedup guard silently skips this click --
        # this tick is just missed, matching "don't overlap syncs" rather
        # than queuing up a backlog of runs.
        self._sync_tab.run_button.click()
        self._schedule_next_run()

    def _update_status_label(self) -> None:
        if self._next_run is None:
            return
        remaining = max(0, int((self._next_run - datetime.now()).total_seconds()))
        self.status_label.setText(
            f"Watching. Next sync at {self._next_run.isoformat(timespec='seconds')} "
            f"(in {remaining}s). Runs only while this window is open."
        )
