"""Shared placeholder widget for tabs not yet built out. Each real tab
module (settings_tab.py, etc.) replaces its PlaceholderTab usage with a real
QWidget as its phase lands; this just keeps the main window's tab list
complete and launchable in the meantime."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlaceholderTab(QWidget):
    def __init__(self, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(message, self)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
