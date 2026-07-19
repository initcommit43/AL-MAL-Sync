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
        tab.watch_interval.setText("6h")

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
