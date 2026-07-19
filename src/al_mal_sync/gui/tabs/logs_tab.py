"""Logs tab: the full al_mal_sync logger stream for the lifetime of the
window (not just one sync run, unlike Sync tab's own live-log panel), with
a verbose toggle."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..log_bridge import PACKAGE_LOGGER_NAME, QtLogHandler


class LogsTab(QWidget):
    def __init__(self, log_handler: QtLogHandler, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.verbose_checkbox = QCheckBox("Verbose (debug logging)", self)
        self.verbose_checkbox.toggled.connect(self._on_verbose_toggled)
        toolbar.addWidget(self.verbose_checkbox)
        self.clear_button = QPushButton("Clear", self)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.clear_button.clicked.connect(self.log_view.clear)
        log_handler.log_emitted.connect(self._append_log_line)

    def _on_verbose_toggled(self, checked: bool) -> None:
        logging.getLogger(PACKAGE_LOGGER_NAME).setLevel(logging.DEBUG if checked else logging.INFO)

    def _append_log_line(self, message: str, _levelno: int) -> None:
        self.log_view.appendPlainText(message)
