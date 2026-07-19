"""Main application window: a tab per major CLI command (login/status ->
Login, sync -> Sync, watch -> Watch, unmapped -> Unmapped, plus a Settings
tab for config.yaml/mappings.yaml editing and a Logs tab)."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from ..config import Config, ConfigError, app_config_dir, default_config_path, load_config
from . import log_bridge
from .tabs.logs_tab import LogsTab
from .tabs.login_tab import LoginTab
from .tabs.mappings_tab import MappingsTab
from .tabs.settings_tab import SettingsTab
from .tabs.sync_tab import SyncTab
from .tabs.unmapped_tab import UnmappedTab
from .tabs.watch_tab import WatchTab

WINDOW_TITLE = "AL-MAL-Sync"

_ABOUT_TEXT = (
    "<b>AL-MAL-Sync</b><br>"
    "Bidirectional sync between AniList and MyAnimeList.<br><br>"
    "Ported from "
    '<a href="https://github.com/bigspawn/anilist-mal-sync">bigspawn/anilist-mal-sync</a> (Go).'
    "<br><br>"
    "Id-mapping data from the "
    '<a href="https://github.com/manami-project/anime-offline-database">anime-offline-database</a>, '
    "Hato, Jikan, and ARM."
)


def _load_initial_config(path: str) -> Config:
    """Missing/incomplete config.yaml is not fatal for the GUI the way it is
    for the CLI -- first-run users have nothing yet, and should land on the
    Settings tab to fill it in rather than seeing a crash."""
    try:
        return load_config(path)
    except ConfigError:
        return Config()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(900, 600)

        # Installed here (not in app.py) so it exists for the lifetime of the
        # main window; the Sync tab's live log panel and the Logs tab both
        # connect to log_handler.log_emitted.
        self.log_handler = log_bridge.install()

        self.config_path = default_config_path()
        self.config = _load_initial_config(self.config_path)

        self.login_tab = LoginTab(self.get_config)
        self.settings_tab = SettingsTab(self.get_config, self.config_path)
        self.sync_tab = SyncTab(self.get_config, self.log_handler)
        self.watch_tab = WatchTab(self.get_config, self.sync_tab)
        self.unmapped_tab = UnmappedTab(self.get_config)
        self.mappings_tab = MappingsTab(self.get_config)
        self.logs_tab = LogsTab(self.log_handler)
        # Login status can go stale after Settings changes which service
        # each button's credentials point at; refresh whenever the user
        # switches back to the Login tab. Watch's schedule display is
        # likewise a read-only mirror of config.watch, set on Settings.
        self.settings_tab.save_button.clicked.connect(self.login_tab.refresh_status)
        self.settings_tab.save_button.clicked.connect(self.watch_tab.refresh_schedule_display)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.login_tab, "Login")
        self.tabs.addTab(self.sync_tab, "Sync")
        self.tabs.addTab(self.watch_tab, "Watch")
        self.tabs.addTab(self.unmapped_tab, "Unmapped")
        self.tabs.addTab(self.mappings_tab, "Mappings")
        self.tabs.addTab(self.logs_tab, "Logs")
        # Unmapped/Mappings reflect on-disk state a sync run can change
        # underneath them; reload from disk whenever the user switches in,
        # rather than requiring a manual Refresh click every time.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self._build_menu()

    def get_config(self) -> Config:
        return self.config

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        reload = getattr(widget, "reload", None)
        if callable(reload):
            reload()

    def _build_menu(self) -> None:
        # Kept as instance attributes, not locals -- a QMenu/QAction with no
        # Python-side reference has been observed to have its wrapper
        # collected (and the underlying widget along with it) even though
        # it's parented to the menu bar/window, the same class of dangling-
        # wrapper issue as the per-row cell widgets in unmapped_tab.py.
        self.help_menu = self.menuBar().addMenu("Help")

        self.open_config_action = QAction("Open Config Folder", self)
        self.open_config_action.triggered.connect(self._open_config_folder)
        self.help_menu.addAction(self.open_config_action)

        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)

    def _open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_config_dir())))

    def _show_about(self) -> None:
        QMessageBox.about(self, "About AL-MAL-Sync", _ABOUT_TEXT)
