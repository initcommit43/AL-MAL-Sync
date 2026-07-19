"""Settings tab: an editable form over config.yaml, mirroring
config.example.yaml, so a non-technical user never has to hand-edit YAML.

Owns no Config of its own -- it's handed the MainWindow's shared Config
instance (via get_config) and mutates it in place on Save, so other tabs
(Login, Sync, ...) that hold the same reference see the change immediately
without any extra notification wiring.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config, ConfigError, save_config

ANILIST_DEV_URL = "https://anilist.co/settings/developer"
MAL_DEV_URL = "https://myanimelist.net/apiconfig"


class SettingsTab(QWidget):
    def __init__(
        self,
        get_config: Callable[[], Config],
        config_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._config_path = config_path

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_anilist_group())
        layout.addWidget(self._build_myanimelist_group())
        layout.addWidget(self._build_sources_group())
        layout.addWidget(self._build_watch_group())

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save", self)
        self.save_button.clicked.connect(self._on_save)
        buttons.addWidget(self.save_button)
        self.status_label = QLabel("", self)
        buttons.addWidget(self.status_label)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

        self._load_from_config()

    # -- form construction --------------------------------------------

    def _build_anilist_group(self) -> QGroupBox:
        group = QGroupBox("AniList Account", self)
        form = QFormLayout(group)
        self.anilist_client_id = QLineEdit(group)
        self.anilist_client_secret = QLineEdit(group)
        self.anilist_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.anilist_username = QLineEdit(group)
        form.addRow("Client ID", self.anilist_client_id)
        form.addRow("Client Secret", self.anilist_client_secret)
        form.addRow("Username", self.anilist_username)

        link = QPushButton("Get AniList API credentials...", group)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ANILIST_DEV_URL)))
        form.addRow("", link)
        return group

    def _build_myanimelist_group(self) -> QGroupBox:
        group = QGroupBox("MyAnimeList Account", self)
        form = QFormLayout(group)
        self.mal_client_id = QLineEdit(group)
        self.mal_client_secret = QLineEdit(group)
        self.mal_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.mal_username = QLineEdit(group)
        form.addRow("Client ID", self.mal_client_id)
        form.addRow("Client Secret", self.mal_client_secret)
        form.addRow("Username", self.mal_username)

        link = QPushButton("Get MyAnimeList API credentials...", group)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(MAL_DEV_URL)))
        form.addRow("", link)
        return group

    def _build_sources_group(self) -> QGroupBox:
        group = QGroupBox("ID-Matching Sources", self)
        form = QFormLayout(group)
        self.offline_db_enabled = QCheckBox("Offline anime database (recommended, anime only)", group)
        self.hato_enabled = QCheckBox("Hato API (recommended, anime + manga)", group)
        self.arm_enabled = QCheckBox("ARM API fallback (opt-in, anime only)", group)
        self.jikan_enabled = QCheckBox("Jikan API (opt-in, manga + MAL favorites)", group)
        self.favorites_enabled = QCheckBox("Sync favorites", group)
        for box in (
            self.offline_db_enabled, self.hato_enabled, self.arm_enabled,
            self.jikan_enabled, self.favorites_enabled,
        ):
            form.addRow(box)
        return group

    def _build_watch_group(self) -> QGroupBox:
        group = QGroupBox("Watch Schedule (used by the Watch tab; leave both blank to disable)", self)
        form = QFormLayout(group)
        self.watch_interval = QLineEdit(group)
        self.watch_interval.setPlaceholderText("e.g. 6h (1h-168h)")
        self.watch_schedule = QLineEdit(group)
        self.watch_schedule.setPlaceholderText("e.g. 0 */6 * * * (cron, 5 fields)")
        form.addRow("Interval", self.watch_interval)
        form.addRow("Cron schedule", self.watch_schedule)
        return group

    # -- load/save -------------------------------------------------------

    def _load_from_config(self) -> None:
        cfg = self._get_config()
        self.anilist_client_id.setText(cfg.anilist.client_id)
        self.anilist_client_secret.setText(cfg.anilist.client_secret)
        self.anilist_username.setText(cfg.anilist.username)
        self.mal_client_id.setText(cfg.myanimelist.client_id)
        self.mal_client_secret.setText(cfg.myanimelist.client_secret)
        self.mal_username.setText(cfg.myanimelist.username)
        self.offline_db_enabled.setChecked(cfg.offline_database.enabled)
        self.hato_enabled.setChecked(cfg.hato_api.enabled)
        self.arm_enabled.setChecked(cfg.arm_api.enabled)
        self.jikan_enabled.setChecked(cfg.jikan_api.enabled)
        self.favorites_enabled.setChecked(cfg.favorites.enabled)
        self.watch_interval.setText(cfg.watch.interval)
        self.watch_schedule.setText(cfg.watch.schedule)

    def _on_save(self) -> None:
        cfg = self._get_config()
        cfg.anilist.client_id = self.anilist_client_id.text().strip()
        cfg.anilist.client_secret = self.anilist_client_secret.text().strip()
        cfg.anilist.username = self.anilist_username.text().strip()
        cfg.myanimelist.client_id = self.mal_client_id.text().strip()
        cfg.myanimelist.client_secret = self.mal_client_secret.text().strip()
        cfg.myanimelist.username = self.mal_username.text().strip()
        cfg.offline_database.enabled = self.offline_db_enabled.isChecked()
        cfg.hato_api.enabled = self.hato_enabled.isChecked()
        cfg.arm_api.enabled = self.arm_enabled.isChecked()
        cfg.jikan_api.enabled = self.jikan_enabled.isChecked()
        cfg.favorites.enabled = self.favorites_enabled.isChecked()
        cfg.watch.interval = self.watch_interval.text().strip()
        cfg.watch.schedule = self.watch_schedule.text().strip()

        try:
            save_config(cfg, self._config_path)
        except (ConfigError, OSError) as exc:
            self.status_label.setText(f"Failed to save: {exc}")
            return
        self.status_label.setText(f"Saved to {self._config_path}")
