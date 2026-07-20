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
from ..widgets import apply_page_layout, cap_width

ANILIST_DEV_URL = "https://anilist.co/settings/developer"
MAL_DEV_URL = "https://myanimelist.net/apiconfig"
_FIELD_WIDTH = 340


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
        apply_page_layout(layout)
        title = QLabel("Settings", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("Your account credentials and how matching/scheduling behaves.", self)
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

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
        form.addRow("Client ID", cap_width(self.anilist_client_id, _FIELD_WIDTH))
        form.addRow("Client Secret", cap_width(self.anilist_client_secret, _FIELD_WIDTH))
        form.addRow("Username", cap_width(self.anilist_username, _FIELD_WIDTH))

        link = QPushButton("Get AniList API credentials...", group)
        link.setObjectName("linkButton")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(ANILIST_DEV_URL)))
        form.addRow("", cap_width(link, _FIELD_WIDTH))
        return group

    def _build_myanimelist_group(self) -> QGroupBox:
        group = QGroupBox("MyAnimeList Account", self)
        form = QFormLayout(group)
        self.mal_client_id = QLineEdit(group)
        self.mal_client_secret = QLineEdit(group)
        self.mal_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.mal_username = QLineEdit(group)
        form.addRow("Client ID", cap_width(self.mal_client_id, _FIELD_WIDTH))
        form.addRow("Client Secret", cap_width(self.mal_client_secret, _FIELD_WIDTH))
        form.addRow("Username", cap_width(self.mal_username, _FIELD_WIDTH))

        link = QPushButton("Get MyAnimeList API credentials...", group)
        link.setObjectName("linkButton")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(MAL_DEV_URL)))
        form.addRow("", cap_width(link, _FIELD_WIDTH))
        return group

    def _build_sources_group(self) -> QGroupBox:
        group = QGroupBox("ID-Matching Sources", self)
        form = QFormLayout(group)
        self.offline_db_enabled = QCheckBox("Offline anime database (recommended, anime only)", group)
        self.offline_db_enabled.setToolTip(
            "Uses a downloaded anime database to match titles between AniList and\n"
            "MyAnimeList. Fast and works without extra API calls -- leave this on."
        )
        self.hato_enabled = QCheckBox("Hato API (recommended, anime + manga)", group)
        self.hato_enabled.setToolTip(
            "An online lookup service that helps match both anime and manga titles.\n"
            "Leave this on for the best match rate."
        )
        self.arm_enabled = QCheckBox("ARM API fallback (opt-in, anime only)", group)
        self.arm_enabled.setToolTip(
            "An extra online lookup used only when the other methods above can't\n"
            "match an anime title. Optional."
        )
        self.jikan_enabled = QCheckBox("Jikan API (opt-in, manga + MAL favorites)", group)
        self.jikan_enabled.setToolTip(
            "An unofficial MyAnimeList data source used to help match manga titles\n"
            "and to read your MAL favorites. Required if you want to sync favorites."
        )
        self.favorites_enabled = QCheckBox("Sync favorites", group)
        self.favorites_enabled.setToolTip(
            "Also keep your favorited anime/manga in sync between AniList and\n"
            "MyAnimeList. Turns on the Jikan API automatically."
        )
        for box in (
            self.offline_db_enabled, self.hato_enabled, self.arm_enabled,
            self.jikan_enabled, self.favorites_enabled,
        ):
            form.addRow(box)
        return group

    def _build_watch_group(self) -> QGroupBox:
        group = QGroupBox("Auto-Sync Schedule (used by the Auto-Sync page; leave both blank to disable)", self)
        form = QFormLayout(group)
        self.watch_interval = QLineEdit(group)
        self.watch_interval.setPlaceholderText("e.g. 6h (1h-168h)")
        self.watch_interval.setToolTip("How often to automatically sync, e.g. \"6h\" for every 6 hours.")
        self.watch_schedule = QLineEdit(group)
        self.watch_schedule.setPlaceholderText("e.g. 0 */6 * * * (cron, 5 fields)")
        self.watch_schedule.setToolTip(
            "Advanced: a cron expression instead of a simple interval.\nLeave blank unless you need specific times."
        )
        form.addRow("Sync every", cap_width(self.watch_interval, _FIELD_WIDTH))
        form.addRow("Cron schedule (advanced)", cap_width(self.watch_schedule, _FIELD_WIDTH))
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
