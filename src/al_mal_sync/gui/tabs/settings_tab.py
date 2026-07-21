"""Settings tab: an editable form over config.yaml, mirroring
config.example.yaml, so a non-technical user never has to hand-edit YAML.

Owns no Config of its own -- it's handed the MainWindow's shared Config
instance (via get_config) and mutates it in place on Save, so other tabs
(Login, Sync, ...) that hold the same reference see the change immediately
without any extra notification wiring.

The Auto-Sync schedule is a spinbox + hour/day dropdown, not a raw duration
string -- a free-text "6h" field (and, worse, a raw 5-field cron expression
right next to it as if it were equally primary) asks a non-technical user to
already know a syntax nobody encounters outside sysadmin tooling. The cron
field still exists for anyone who genuinely needs exact times of day, but
it's demoted into a collapsed "Advanced" section and explicitly documented
as an override of the friendly composer above it.

The "About" section (Open Config Folder / About dialog) lives here rather
than in a top menu bar: a floating "Help" menu with no visual relationship
to the sidebar-driven rest of the app read as a leftover, not a real part of
the UI -- folding it into Settings' own footer keeps everything reachable
from the same nav the rest of the app uses.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...config import Config, ConfigError, app_config_dir, parse_duration, save_config
from ..widgets import CollapsibleSection, apply_page_layout, cap_width

ANILIST_DEV_URL = "https://anilist.co/settings/developer"
MAL_DEV_URL = "https://myanimelist.net/apiconfig"
_FIELD_WIDTH = 340

_HOURS_INDEX = 0
_DAYS_INDEX = 1
_MAX_HOURS = 168
_MAX_DAYS = 7

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


def _hours_from_interval(interval: str) -> int | None:
    """Best-effort parse for populating the composer from an existing
    config -- returns None (composer falls back to its default) for
    anything that isn't a plain duration string, rather than raising."""
    try:
        duration = parse_duration(interval)
    except ConfigError:
        return None
    return round(duration.total_seconds() / 3600)


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
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._on_save)
        buttons.addWidget(self.save_button)
        self.status_label = QLabel("", self)
        buttons.addWidget(self.status_label)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addWidget(self._build_about_group())
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
        group = QGroupBox("Auto-Sync Schedule", self)
        layout = QVBoxLayout(group)

        self.autosync_enabled_checkbox = QCheckBox("Automatically sync on a schedule", group)
        self.autosync_enabled_checkbox.setToolTip(
            "Turns on the schedule below. The Auto-Sync page then runs a sync for you\n"
            "at this interval, as long as the app stays open."
        )
        self.autosync_enabled_checkbox.toggled.connect(self._on_autosync_enabled_toggled)
        layout.addWidget(self.autosync_enabled_checkbox)

        every_row = QHBoxLayout()
        every_row.addWidget(QLabel("Sync every", group))
        self.autosync_amount_spin = QSpinBox(group)
        self.autosync_amount_spin.setRange(1, _MAX_HOURS)
        every_row.addWidget(self.autosync_amount_spin)
        self.autosync_unit_combo = QComboBox(group)
        self.autosync_unit_combo.addItems(["hour(s)", "day(s)"])
        self.autosync_unit_combo.currentIndexChanged.connect(self._on_autosync_unit_changed)
        every_row.addWidget(self.autosync_unit_combo)
        every_row.addStretch(1)
        layout.addLayout(every_row)

        advanced = QWidget(group)
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(4, 8, 4, 4)
        self.watch_schedule = QLineEdit(advanced)
        self.watch_schedule.setPlaceholderText("e.g. 0 3 * * * (cron, 5 fields)")
        self.watch_schedule.setToolTip(
            "For exact times of day instead of a plain interval (e.g. \"every day at\n"
            "3am\"). Overrides \"Sync every\" above when filled in. Leave blank unless\n"
            "you specifically need this."
        )
        advanced_form.addRow("Cron schedule", cap_width(self.watch_schedule, _FIELD_WIDTH))
        self.autosync_advanced_section = CollapsibleSection(
            "Advanced: exact cron schedule (overrides the above)", advanced, collapsed=True
        )
        layout.addWidget(self.autosync_advanced_section)

        # toggled only fires on an actual state *change* -- the checkbox's
        # own default (unchecked) never triggers it, so without this the
        # composer below would start enabled despite the checkbox reading
        # unchecked, until the user clicked it once.
        self._on_autosync_enabled_toggled(self.autosync_enabled_checkbox.isChecked())

        return group

    def _build_about_group(self) -> QGroupBox:
        group = QGroupBox("About", self)
        row = QHBoxLayout(group)
        self.open_config_button = QPushButton("Open Config Folder", group)
        self.open_config_button.clicked.connect(self._on_open_config_folder)
        row.addWidget(self.open_config_button)
        self.about_button = QPushButton("About AL-MAL-Sync", group)
        self.about_button.clicked.connect(self._on_show_about)
        row.addWidget(self.about_button)
        row.addStretch(1)
        return group

    # -- Auto-Sync composer ------------------------------------------------

    def _on_autosync_enabled_toggled(self, checked: bool) -> None:
        self.autosync_amount_spin.setEnabled(checked)
        self.autosync_unit_combo.setEnabled(checked)
        self.autosync_advanced_section.setEnabled(checked)

    def _on_autosync_unit_changed(self, index: int) -> None:
        # setMaximum() clamps an out-of-range current value automatically,
        # so switching units never needs to convert/round the amount itself
        # -- e.g. going from 6 hours to the day unit just caps it at 6->7.
        self.autosync_amount_spin.setMaximum(_MAX_DAYS if index == _DAYS_INDEX else _MAX_HOURS)

    # -- about actions ------------------------------------------------------

    def _on_open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_config_dir())))

    def _on_show_about(self) -> None:
        QMessageBox.about(self, "About AL-MAL-Sync", _ABOUT_TEXT)

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
        self._load_watch_into_form(cfg.watch.interval, cfg.watch.schedule)

    def _load_watch_into_form(self, interval: str, schedule: str) -> None:
        self.watch_schedule.setText(schedule)
        if schedule:
            self.autosync_enabled_checkbox.setChecked(True)
            self.autosync_advanced_section.set_collapsed(False)
            return

        hours = _hours_from_interval(interval) if interval else None
        if hours is None:
            self.autosync_enabled_checkbox.setChecked(bool(interval))
            return

        self.autosync_enabled_checkbox.setChecked(True)
        if hours >= 24 and hours % 24 == 0:
            self.autosync_unit_combo.setCurrentIndex(_DAYS_INDEX)
            self.autosync_amount_spin.setMaximum(_MAX_DAYS)
            self.autosync_amount_spin.setValue(hours // 24)
        else:
            self.autosync_unit_combo.setCurrentIndex(_HOURS_INDEX)
            self.autosync_amount_spin.setMaximum(_MAX_HOURS)
            self.autosync_amount_spin.setValue(hours)

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
        cfg.watch.interval, cfg.watch.schedule = self._read_watch_from_form()

        try:
            save_config(cfg, self._config_path)
        except (ConfigError, OSError) as exc:
            self.status_label.setText(f"Failed to save: {exc}")
            return
        self.status_label.setText(f"Saved to {self._config_path}")

    def _read_watch_from_form(self) -> tuple[str, str]:
        if not self.autosync_enabled_checkbox.isChecked():
            return "", ""
        cron = self.watch_schedule.text().strip()
        if cron:
            return "", cron
        amount = self.autosync_amount_spin.value()
        hours = amount * 24 if self.autosync_unit_combo.currentIndex() == _DAYS_INDEX else amount
        return f"{hours}h", ""
