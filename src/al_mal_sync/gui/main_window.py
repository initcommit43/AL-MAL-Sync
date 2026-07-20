"""Main application window: a left sidebar (Dashboard first, Settings last)
driving a stacked page area -- Dashboard, Sync, Login, Auto-Sync, Mapping
Issues, Logs, Settings."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, ConfigError, app_config_dir, default_config_path, load_config
from . import log_bridge
from .tabs.auto_sync_tab import AutoSyncTab
from .tabs.dashboard_tab import DashboardTab
from .tabs.logs_tab import LogsTab
from .tabs.login_tab import LoginTab
from .tabs.mapping_issues_tab import MappingIssuesTab
from .tabs.settings_tab import SettingsTab
from .tabs.sync_tab import SyncTab

WINDOW_TITLE = "AL-MAL-Sync"
SIDEBAR_WIDTH = 200

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
    Settings page to fill it in rather than seeing a crash."""
    try:
        return load_config(path)
    except ConfigError:
        return Config()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1000, 650)

        # Installed here (not in app.py) so it exists for the lifetime of the
        # main window; the Sync tab's live log panel and the Logs tab both
        # connect to log_handler.log_emitted.
        self.log_handler = log_bridge.install()

        self.config_path = default_config_path()
        self.config = _load_initial_config(self.config_path)

        self.dashboard_tab = DashboardTab(self.get_config)
        self.sync_tab = SyncTab(self.get_config, self.log_handler)
        self.login_tab = LoginTab(self.get_config)
        self.auto_sync_tab = AutoSyncTab(self.get_config, self.sync_tab)
        self.mapping_issues_tab = MappingIssuesTab(self.get_config)
        self.logs_tab = LogsTab(self.log_handler)
        self.settings_tab = SettingsTab(self.get_config, self.config_path)

        # Dashboard-first, Settings-last: the order a casual user should meet
        # these pages in, not the order they were built in.
        self._pages: list[tuple[str, str, QWidget]] = [
            ("dashboard", "Dashboard", self.dashboard_tab),
            ("sync", "Sync", self.sync_tab),
            ("login", "Login", self.login_tab),
            ("auto_sync", "Auto-Sync", self.auto_sync_tab),
            ("mapping_issues", "Mapping Issues", self.mapping_issues_tab),
            ("logs", "Logs", self.logs_tab),
            ("settings", "Settings", self.settings_tab),
        ]
        self._page_index_by_key = {key: i for i, (key, _label, _widget) in enumerate(self._pages)}

        self.nav_list = QListWidget(self)
        self.nav_list.setObjectName("sidebar")
        self.stack = QStackedWidget(self)
        for _key, label, widget in self._pages:
            QListWidgetItem(label, self.nav_list)
            self.stack.addWidget(widget)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)

        self.setCentralWidget(self._build_central_widget())
        self.nav_list.setCurrentRow(0)

        # Login status/dashboard/auto-sync's schedule display can go stale
        # after Settings changes which service each button's credentials
        # point at, or after a login/logout/sync happens elsewhere -- refresh
        # them on whichever event actually changed what they show, rather
        # than requiring a manual click every time.
        self.settings_tab.save_button.clicked.connect(self.login_tab.refresh_status)
        self.settings_tab.save_button.clicked.connect(self.auto_sync_tab.refresh_schedule_display)
        self.settings_tab.save_button.clicked.connect(self.dashboard_tab.reload)
        self.login_tab.auth_changed.connect(self.dashboard_tab.reload)
        self.sync_tab.sync_finished.connect(self.dashboard_tab.reload)
        self.dashboard_tab.navigate_requested.connect(self._navigate_to)

        self._build_menu()

    def _build_central_widget(self) -> QWidget:
        sidebar_container = QWidget(self)
        sidebar_container.setObjectName("sidebarContainer")
        sidebar_container.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_layout = QVBoxLayout(sidebar_container)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        app_title = QLabel("AL-MAL-Sync", sidebar_container)
        app_title.setObjectName("sidebarTitle")
        sidebar_layout.addWidget(app_title)
        sidebar_layout.addWidget(self.nav_list, 1)

        central = QWidget(self)
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(sidebar_container)
        central_layout.addWidget(self.stack, 1)
        return central

    def get_config(self) -> Config:
        return self.config

    def _navigate_to(self, key: str) -> None:
        index = self._page_index_by_key.get(key)
        if index is not None:
            self.nav_list.setCurrentRow(index)

    def _on_nav_changed(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        # Several pages reflect on-disk state a sync run (or another page)
        # can change underneath them; reload from disk whenever the user
        # switches in, rather than requiring a manual Refresh click.
        widget = self.stack.widget(index)
        reload = getattr(widget, "reload", None)
        if callable(reload):
            reload()

    def _build_menu(self) -> None:
        # Kept as instance attributes, not locals -- a QMenu/QAction with no
        # Python-side reference has been observed to have its wrapper
        # collected (and the underlying widget along with it) even though
        # it's parented to the menu bar/window, the same class of dangling-
        # wrapper issue as the per-row cell widgets in mapping_issues_tab.py.
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
