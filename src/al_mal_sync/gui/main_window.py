"""Main application window: a tab per major CLI command (login/status ->
Login, sync -> Sync, watch -> Watch, unmapped -> Unmapped, plus a Settings
tab for config.yaml/mappings.yaml editing and a Logs tab). Phases D-G still
replace the remaining placeholders with real functionality."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget

from ..config import Config, ConfigError, default_config_path, load_config
from . import log_bridge
from .tabs._placeholder import PlaceholderTab
from .tabs.login_tab import LoginTab
from .tabs.settings_tab import SettingsTab

WINDOW_TITLE = "AL-MAL-Sync"


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
        # main window and later phases can wire log_bridge.log_emitted into
        # the Logs tab / Sync screen's live log panel.
        self.log_handler = log_bridge.install()

        self.config_path = default_config_path()
        self.config = _load_initial_config(self.config_path)

        self.login_tab = LoginTab(self.get_config)
        self.settings_tab = SettingsTab(self.get_config, self.config_path)
        # Login status can go stale after Settings changes which service
        # each button's credentials point at; refresh whenever the user
        # switches back to the Login tab.
        self.settings_tab.save_button.clicked.connect(self.login_tab.refresh_status)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self.login_tab, "Login")
        self.tabs.addTab(PlaceholderTab("Sync -- coming soon"), "Sync")
        self.tabs.addTab(PlaceholderTab("Watch -- coming soon"), "Watch")
        self.tabs.addTab(PlaceholderTab("Unmapped -- coming soon"), "Unmapped")
        self.tabs.addTab(PlaceholderTab("Mappings -- coming soon"), "Mappings")
        self.tabs.addTab(PlaceholderTab("Logs -- coming soon"), "Logs")
        self.setCentralWidget(self.tabs)

    def get_config(self) -> Config:
        return self.config
