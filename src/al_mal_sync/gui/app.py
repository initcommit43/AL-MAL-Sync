"""QApplication bootstrap. `al-mal-sync-gui` (see pyproject.toml
[project.scripts]) and `python -m al_mal_sync.gui` both enter here."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import STYLESHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("AL-MAL-Sync")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
