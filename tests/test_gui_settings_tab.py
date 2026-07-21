"""Tests for gui/tabs/settings_tab.py: loading a Config into the form and
saving edits back out. Uses a real QApplication (offscreen, via conftest.py)
and real widgets -- no pytest-qt needed for simple setText/click/isChecked
calls, which don't require simulated event delivery."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config, load_config  # noqa: E402
from al_mal_sync.gui.tabs.settings_tab import SettingsTab  # noqa: E402

# qt_app fixture is shared from conftest.py.


class TestSettingsTab:
    def test_loads_existing_config_into_form(self, qt_app: QApplication) -> None:
        cfg = Config()
        cfg.anilist.client_id = "ani_id"
        cfg.anilist.username = "ani_user"
        cfg.myanimelist.client_id = "mal_id"
        cfg.hato_api.enabled = False
        cfg.favorites.enabled = True

        tab = SettingsTab(lambda: cfg, "unused.yaml")

        assert tab.anilist_client_id.text() == "ani_id"
        assert tab.anilist_username.text() == "ani_user"
        assert tab.mal_client_id.text() == "mal_id"
        assert tab.hato_enabled.isChecked() is False
        assert tab.favorites_enabled.isChecked() is True

    def test_save_writes_form_values_and_is_loadable(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in (
            "ANILIST_CLIENT_ID", "ANILIST_USERNAME", "MAL_CLIENT_ID", "MAL_USERNAME",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = Config()
        config_path = tmp_path / "config.yaml"
        tab = SettingsTab(lambda: cfg, str(config_path))

        tab.anilist_client_id.setText("new_ani_id")
        tab.anilist_username.setText("new_ani_user")
        tab.mal_client_id.setText("new_mal_id")
        tab.mal_username.setText("new_mal_user")
        tab.arm_enabled.setChecked(True)
        tab.autosync_enabled_checkbox.setChecked(True)
        tab.autosync_unit_combo.setCurrentIndex(0)  # hour(s)
        tab.autosync_amount_spin.setValue(6)

        tab.save_button.click()

        assert config_path.exists()
        assert "Saved" in tab.status_label.text()

        reloaded = load_config(config_path)
        assert reloaded.anilist.client_id == "new_ani_id"
        assert reloaded.myanimelist.username == "new_mal_user"
        assert reloaded.arm_api.enabled is True
        assert reloaded.watch.interval == "6h"

    def test_save_mutates_the_shared_config_instance(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        """Settings mutates the Config object returned by get_config() in
        place, so any other tab holding the same reference (Login, Sync, ...)
        sees the update without extra plumbing."""
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.anilist_client_id.setText("mutated_id")
        tab.save_button.click()

        assert cfg.anilist.client_id == "mutated_id"


class TestAutoSyncComposer:
    def test_unchecked_by_default_writes_no_schedule(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.save_button.click()

        assert cfg.watch.interval == ""
        assert cfg.watch.schedule == ""

    def test_composer_starts_disabled_when_checkbox_is_unchecked(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        """Regression test: QCheckBox.toggled only fires on an actual state
        *change* -- the checkbox's own default (unchecked) never fires it,
        so the composer widgets need their disabled state set explicitly at
        construction instead of relying on the toggled signal alone."""
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        assert tab.autosync_enabled_checkbox.isChecked() is False
        assert tab.autosync_amount_spin.isEnabled() is False
        assert tab.autosync_unit_combo.isEnabled() is False

    def test_composer_enabled_when_a_saved_interval_loads_checked(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        cfg.watch.interval = "6h"

        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        assert tab.autosync_amount_spin.isEnabled() is True
        assert tab.autosync_unit_combo.isEnabled() is True

    def test_days_unit_multiplies_into_hours(self, qt_app: QApplication, tmp_path: Path) -> None:
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.autosync_enabled_checkbox.setChecked(True)
        tab.autosync_unit_combo.setCurrentIndex(1)  # day(s)
        tab.autosync_amount_spin.setValue(2)
        tab.save_button.click()

        assert cfg.watch.interval == "48h"
        assert cfg.watch.schedule == ""

    def test_advanced_cron_field_overrides_the_composer_when_filled_in(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.autosync_enabled_checkbox.setChecked(True)
        tab.autosync_amount_spin.setValue(6)
        tab.watch_schedule.setText("0 3 * * *")
        tab.save_button.click()

        assert cfg.watch.schedule == "0 3 * * *"
        assert cfg.watch.interval == ""

    def test_unchecking_clears_a_previously_saved_schedule(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        cfg.watch.interval = "6h"
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))
        assert tab.autosync_enabled_checkbox.isChecked() is True

        tab.autosync_enabled_checkbox.setChecked(False)
        tab.save_button.click()

        assert cfg.watch.interval == ""
        assert cfg.watch.schedule == ""

    def test_loads_hourly_interval_into_the_composer(self, qt_app: QApplication, tmp_path: Path) -> None:
        cfg = Config()
        cfg.watch.interval = "6h"

        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        assert tab.autosync_enabled_checkbox.isChecked() is True
        assert tab.autosync_unit_combo.currentIndex() == 0
        assert tab.autosync_amount_spin.value() == 6

    def test_loads_day_aligned_interval_into_the_composer_as_days(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        cfg.watch.interval = "48h"

        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        assert tab.autosync_unit_combo.currentIndex() == 1
        assert tab.autosync_amount_spin.value() == 2

    def test_loads_existing_cron_schedule_and_expands_advanced_section(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        cfg = Config()
        cfg.watch.schedule = "0 3 * * *"

        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        assert tab.autosync_enabled_checkbox.isChecked() is True
        assert tab.watch_schedule.text() == "0 3 * * *"
        assert tab.autosync_advanced_section.content.isHidden() is False


class TestAboutSection:
    def test_open_config_folder_button_calls_qdesktopservices(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.settings_tab.QDesktopServices.openUrl",
            lambda url: calls.append(url.toLocalFile()),
        )
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.open_config_button.click()

        assert len(calls) == 1

    def test_about_button_shows_dialog(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.settings_tab.QMessageBox.about",
            lambda parent, title, text: calls.append((title, text)),
        )
        cfg = Config()
        tab = SettingsTab(lambda: cfg, str(tmp_path / "config.yaml"))

        tab.about_button.click()

        assert len(calls) == 1
        assert calls[0][0] == "About AL-MAL-Sync"
