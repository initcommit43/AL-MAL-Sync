"""Tests for gui/tabs/mappings_tab.py: loading mappings.yaml into the
tables and saving edits back out via the same MappingsConfig.save() the CLI
uses. mappings_file_path always points at tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs.mappings_tab import MappingsTab  # noqa: E402
from al_mal_sync.mapping.manual_mappings import (  # noqa: E402
    IgnoreConfig,
    ManualMapping,
    MappingsConfig,
    load_mappings,
)

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.mappings_file_path = str(tmp_path / "mappings.yaml")
    return cfg


class TestMappingsTab:
    def test_loads_existing_manual_mappings_and_ignore_lists(
        self, qt_app: QApplication, config: Config
    ) -> None:
        mappings = MappingsConfig(
            manual_mappings=[ManualMapping(anilist_id=1, mal_id=2, comment="note")],
            ignore=IgnoreConfig(anilist_ids=[5], mal_ids=[6], titles=["Foo"]),
        )
        mappings.save(config.mappings_file_path)

        tab = MappingsTab(lambda: config)

        assert tab.manual_table.rowCount() == 1
        assert tab.manual_table.item(0, 0).text() == "1"
        assert tab.manual_table.item(0, 1).text() == "2"
        assert tab.manual_table.item(0, 2).text() == "note"
        assert tab.ignore_table.rowCount() == 3

    def test_save_writes_manual_mapping_added_via_add_row(
        self, qt_app: QApplication, config: Config
    ) -> None:
        tab = MappingsTab(lambda: config)

        tab._add_manual_row()
        tab.manual_table.item(0, 0).setText("100")
        tab.manual_table.item(0, 1).setText("200")
        tab.manual_table.item(0, 2).setText("manual note")
        tab.save_button.click()

        assert "Saved" in tab.status_label.text()
        reloaded = load_mappings(config.mappings_file_path)
        assert reloaded.get_manual_mal_id(100) == 200
        assert reloaded.manual_mappings[0].comment == "manual note"

    def test_save_writes_ignore_rows_split_by_type(
        self, qt_app: QApplication, config: Config
    ) -> None:
        from PySide6.QtWidgets import QComboBox

        tab = MappingsTab(lambda: config)

        tab._add_ignore_row()
        combo = tab.ignore_table.cellWidget(0, 0)
        assert isinstance(combo, QComboBox)
        combo.setCurrentText("AniList ID")
        tab.ignore_table.item(0, 1).setText("77")

        tab._add_ignore_row()
        tab.ignore_table.cellWidget(1, 0).setCurrentText("Title")
        tab.ignore_table.item(1, 1).setText("Some Title")

        tab.save_button.click()

        reloaded = load_mappings(config.mappings_file_path)
        assert reloaded.ignore.anilist_ids == [77]
        assert reloaded.ignore.titles == ["Some Title"]

    def test_save_with_non_numeric_manual_id_shows_error_and_does_not_save(
        self, qt_app: QApplication, config: Config
    ) -> None:
        tab = MappingsTab(lambda: config)
        tab._add_manual_row()
        tab.manual_table.item(0, 0).setText("not-a-number")

        tab.save_button.click()

        assert "must be numbers" in tab.status_label.text()
        assert not Path(config.mappings_file_path).exists()

    def test_remove_selected_rows(self, qt_app: QApplication, config: Config) -> None:
        tab = MappingsTab(lambda: config)
        tab._add_manual_row()
        tab._add_manual_row()
        tab.manual_table.selectRow(0)

        tab._remove_selected_rows(tab.manual_table)

        assert tab.manual_table.rowCount() == 1
