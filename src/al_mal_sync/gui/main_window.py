"""Main application window: a left sidebar (Dashboard first, Settings last)
driving a stacked page area -- Dashboard, Auto-Sync, Manual Sync, Login,
Mapping Issues, Settings. Logs and the Help menu both used to be their own
top-level things (a separate Logs page, a floating menu-bar item); Logs is
now folded into the bottom of the Auto-Sync page (see sync_tab.py) and Help
lives in Settings' "About" section -- both were only ever relevant *from*
another page, not independent destinations of their own.

There used to be a scheduling page here too (a GUI QTimer loop toggled by a
button) but it only ever ran while this window's process stayed alive, which
made it useless for the entire point of "automatic" -- syncing while the PC
is off or the app isn't open. That's dropped in favor of pointing anyone who
wants real unattended scheduling at the CLI's `watch` command or Docker
(config.watch is still configured from Settings), rather than keeping a GUI
page around that only worked while you were already sitting in front of it.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, ConfigError, default_config_path, load_config
from . import log_bridge
from .tabs.dashboard_tab import DashboardTab
from .tabs.export_import_tab import ExportImportTab
from .tabs.login_tab import LoginTab
from .tabs.mapping_issues_tab import MappingIssuesTab
from .tabs.settings_tab import SettingsTab
from .tabs.sync_tab import SyncTab
from .theme import ACCENT

WINDOW_TITLE = "AL-MAL-Sync"
SIDEBAR_WIDTH = 200


def _load_initial_config(path: str) -> Config:
    """Missing/incomplete config.yaml is not fatal for the GUI the way it is
    for the CLI -- first-run users have nothing yet, and should land on the
    Settings page to fill it in rather than seeing a crash.

    validate=False is load-bearing, not cosmetic: without it, load_config()
    raises (and this falls back to a blank Config()) whenever even one
    unrelated required field is still empty -- silently discarding a
    perfectly valid, already-saved username (or client ID, or anything else
    filled in so far) back to nothing on every single restart. The CLI needs
    that strict gate; the GUI must tolerate and preserve incremental
    progress instead of erasing it.
    """
    try:
        return load_config(path, validate=False)
    except ConfigError:
        return Config()


def _build_app_icon() -> QIcon:
    """A plain generated monogram (no bundled icon asset) -- an accent-blue
    circle with "AM" in it, used as the window icon."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(ACCENT))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(0, 0, size, size))
    painter.setPen(QColor("#0b1622"))
    font = QFont("Segoe UI", 22, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "AM")
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1000, 650)
        self._app_icon = _build_app_icon()
        self.setWindowIcon(self._app_icon)

        # Installed here (not in app.py) so it exists for the lifetime of the
        # main window; the Auto-Sync tab's live log panel and the Logs tab
        # both connect to log_handler.log_emitted.
        self.log_handler = log_bridge.install()

        self.config_path = default_config_path()
        self.config = _load_initial_config(self.config_path)

        self.dashboard_tab = DashboardTab(self.get_config)
        self.sync_tab = SyncTab(self.get_config, self.log_handler)
        self.export_import_tab = ExportImportTab(self.get_config)
        self.login_tab = LoginTab(self.get_config, self.config_path)
        self.mapping_issues_tab = MappingIssuesTab(self.get_config)
        self.settings_tab = SettingsTab(self.get_config, self.config_path)

        # Dashboard-first, Settings-last: the order a casual user should meet
        # these pages in, not the order they were built in.
        self._pages: list[tuple[str, str, QWidget]] = [
            ("dashboard", "Dashboard", self.dashboard_tab),
            ("sync", "Auto-Sync", self.sync_tab),
            ("export_import", "Manual Sync", self.export_import_tab),
            ("login", "Login", self.login_tab),
            ("mapping_issues", "Mapping Issues", self.mapping_issues_tab),
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

        # Login status/dashboard can go stale after Settings changes which
        # service each button's credentials point at, or after a
        # login/logout/sync happens elsewhere -- refresh them on whichever
        # event actually changed what they show, rather than requiring a
        # manual click every time.
        self.settings_tab.save_button.clicked.connect(self.login_tab.refresh_status)
        self.settings_tab.save_button.clicked.connect(self.dashboard_tab.reload)
        self.login_tab.auth_changed.connect(self.dashboard_tab.reload)
        self.sync_tab.sync_finished.connect(self.dashboard_tab.reload)
        self.dashboard_tab.navigate_requested.connect(self._navigate_to)

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
