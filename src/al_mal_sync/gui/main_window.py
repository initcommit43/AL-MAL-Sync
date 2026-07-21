"""Main application window: a left sidebar (Dashboard first, Settings last)
driving a stacked page area -- Dashboard, Sync, Login, Auto-Sync, Mapping
Issues, Settings. Logs and the Help menu both used to be their own top-level
things (a separate Logs page, a floating menu-bar item); Logs is now folded
into the bottom of the Sync page (see sync_tab.py) and Help lives in
Settings' "About" section -- both were only ever relevant *from* another
page, not independent destinations of their own.

Also owns the system tray icon: closing the window while Auto-Sync is
running hides it instead of quitting, so Auto-Sync keeps ticking in the
background without the window needing to stay open -- the whole point of
having a GUI toggle for it, rather than that toggle being effectively
decorative and pointing people at the CLI/Docker for anything unattended.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QCloseEvent, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, ConfigError, default_config_path, load_config
from . import log_bridge
from .tabs.auto_sync_tab import AutoSyncTab
from .tabs.dashboard_tab import DashboardTab
from .tabs.login_tab import LoginTab
from .tabs.mapping_issues_tab import MappingIssuesTab
from .tabs.settings_tab import SettingsTab
from .tabs.sync_tab import SyncTab
from .theme import ACCENT

WINDOW_TITLE = "AL-MAL-Sync"
SIDEBAR_WIDTH = 200
_TRAY_NOTIFICATION_MS = 6000


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
    circle with "AM" in it, used as both the window icon and the tray icon
    so the tray entry is visually traceable back to this app."""
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
        # Set only by the tray menu's Quit action -- lets closeEvent tell
        # "the user asked to quit" apart from "the window's X button was
        # clicked while Auto-Sync is running", which should minimize to the
        # tray instead of actually exiting.
        self._force_quit = False

        # Installed here (not in app.py) so it exists for the lifetime of the
        # main window; the Sync tab's live log panel and the Logs tab both
        # connect to log_handler.log_emitted.
        self.log_handler = log_bridge.install()

        self.config_path = default_config_path()
        self.config = _load_initial_config(self.config_path)

        self.dashboard_tab = DashboardTab(self.get_config)
        self.sync_tab = SyncTab(self.get_config, self.log_handler)
        self.login_tab = LoginTab(self.get_config, self.config_path)
        self.auto_sync_tab = AutoSyncTab(self.get_config, self.sync_tab)
        self.mapping_issues_tab = MappingIssuesTab(self.get_config)
        self.settings_tab = SettingsTab(self.get_config, self.config_path)

        # Dashboard-first, Settings-last: the order a casual user should meet
        # these pages in, not the order they were built in.
        self._pages: list[tuple[str, str, QWidget]] = [
            ("dashboard", "Dashboard", self.dashboard_tab),
            ("sync", "Sync", self.sync_tab),
            ("login", "Login", self.login_tab),
            ("auto_sync", "Auto-Sync", self.auto_sync_tab),
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

        self.tray_icon = self._build_tray_icon()

    def _build_tray_icon(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None

        # Kept as an instance attribute (self.tray_menu), same reasoning as
        # main_window.py's old _build_menu docstring about QMenu/QAction
        # wrappers being collected without a surviving Python reference.
        self.tray_menu = QMenu(self)
        self.tray_show_action = self.tray_menu.addAction("Show AL-MAL-Sync")
        self.tray_show_action.triggered.connect(self._show_from_tray)
        self.tray_quit_action = self.tray_menu.addAction("Quit")
        self.tray_quit_action.triggered.connect(self._quit_from_tray)

        tray_icon = QSystemTrayIcon(self._app_icon, self)
        tray_icon.setToolTip(WINDOW_TITLE)
        tray_icon.setContextMenu(self.tray_menu)
        tray_icon.activated.connect(self._on_tray_activated)
        tray_icon.show()
        return tray_icon

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Auto-Sync only actually runs while some process is alive to host
        # its QTimer -- closing the window used to mean either leaving it
        # open forever or losing the schedule entirely. Minimizing to the
        # tray instead means the GUI toggle is a real substitute for the
        # CLI's `watch` command, not just a demo of it.
        if self.tray_icon is not None and not self._force_quit and self.auto_sync_tab.is_watching:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                WINDOW_TITLE,
                "Still running in the background -- Auto-Sync keeps going. "
                "Right-click the tray icon to reopen or quit.",
                QSystemTrayIcon.MessageIcon.Information,
                _TRAY_NOTIFICATION_MS,
            )
            return
        event.accept()

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
