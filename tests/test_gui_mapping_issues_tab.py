"""Tests for gui/tabs/mapping_issues_tab.py: the "Needs your attention"
table (formerly unmapped_tab.py) and the "Manual overrides" section
(formerly mappings_tab.py), now one merged page. Each action still calls the
exact same MappingsConfig/UnmappedState primitives cli.py's
`unmapped --fix` loop does. mappings_file_path and the unmapped state path
always point at tmp_path -- never real user files."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs.mapping_issues_tab import MappingIssuesTab  # noqa: E402
from al_mal_sync.mapping.manual_mappings import (  # noqa: E402
    IgnoreConfig,
    ManualMapping,
    MappingsConfig,
    load_mappings,
)
from al_mal_sync.unmapped import UnmappedRecord, UnmappedState, save_unmapped_state  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    cfg = Config()
    cfg.mappings_file_path = str(tmp_path / "mappings.yaml")
    # resolved_unmapped_state_path has no config field to override (unlike
    # token/mappings paths) -- it always calls default_unmapped_state_path(),
    # imported separately into both config.py (for the Config property) and
    # unmapped.py (for load/save's own default). Both must be patched so
    # nothing here ever touches the real per-user state file.
    state_path = str(tmp_path / "unmapped.json")
    monkeypatch.setattr("al_mal_sync.config.default_unmapped_state_path", lambda: state_path)
    monkeypatch.setattr("al_mal_sync.unmapped.default_unmapped_state_path", lambda: state_path)
    return cfg


def _entry(**overrides: object) -> UnmappedRecord:
    defaults: dict[str, object] = {
        "title": "Some Show", "anilist_id": 10, "mal_id": 0, "media_type": "anime",
        "direction": "forward", "reason": "no strategy matched", "updated_at": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return UnmappedRecord(**defaults)  # type: ignore[arg-type]


def _write_state(config: Config, *entries: UnmappedRecord) -> None:
    save_unmapped_state(UnmappedState(entries=list(entries)), config.resolved_unmapped_state_path)


class TestNeedsAttentionSection:
    def test_reload_renders_entries(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry())

        tab = MappingIssuesTab(lambda: config)

        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 0).text() == "Some Show"

    def test_direction_rendered_in_plain_language(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry(direction="forward"), _entry(title="Other", direction="reverse"))

        tab = MappingIssuesTab(lambda: config)

        assert tab.table.item(0, 2).text() == "AniList -> MyAnimeList"
        assert tab.table.item(1, 2).text() == "MyAnimeList -> AniList"

    def test_ignore_by_id_removes_row_and_updates_mappings(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry(anilist_id=42))
        tab = MappingIssuesTab(lambda: config)

        tab._ignore_by_id(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert 42 in mappings.ignore.anilist_ids

    def test_ignore_by_title_removes_row_and_updates_mappings(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(title="Weird Title", anilist_id=0))
        tab = MappingIssuesTab(lambda: config)

        tab._ignore_by_title(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert "Weird Title" in mappings.ignore.titles

    def test_map_manually_adds_manual_mapping_and_removes_row(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(config, _entry(anilist_id=7))
        tab = MappingIssuesTab(lambda: config)

        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.mapping_issues_tab.QInputDialog.getInt",
            lambda *a, **kw: (999, True),
        )
        tab._map_manually(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert mappings.get_manual_mal_id(7) == 999

    def test_map_manually_updates_manual_overrides_table_too(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(config, _entry(anilist_id=7))
        tab = MappingIssuesTab(lambda: config)

        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.mapping_issues_tab.QInputDialog.getInt",
            lambda *a, **kw: (999, True),
        )
        tab._map_manually(tab._state.entries[0])

        assert tab.manual_table.rowCount() == 1
        assert tab.manual_table.item(0, 1).text() == "999"

    def test_ignore_all_clears_every_row(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry(anilist_id=1), _entry(anilist_id=2, title="Other"))
        tab = MappingIssuesTab(lambda: config)

        tab._on_ignore_all()

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert set(mappings.ignore.anilist_ids) == {1, 2}

    def test_no_selection_ignore_button_click_is_a_no_op(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry(anilist_id=1))
        tab = MappingIssuesTab(lambda: config)

        tab.ignore_id_button.click()  # nothing selected

        assert tab.table.rowCount() == 1
        assert not Path(config.mappings_file_path).exists()

    def test_selecting_row_then_clicking_ignore_id_button_removes_it(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=1), _entry(anilist_id=2, title="Other"))
        tab = MappingIssuesTab(lambda: config)
        tab.table.selectRow(0)

        tab.ignore_id_button.click()

        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 0).text() == "Other"
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert mappings.ignore.anilist_ids == [1]

    def test_ignore_id_button_disabled_action_skipped_when_entry_has_no_ids(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=0, mal_id=0))
        tab = MappingIssuesTab(lambda: config)
        tab.table.selectRow(0)

        tab.ignore_id_button.click()

        # No id to ignore by -- row stays, nothing written.
        assert tab.table.rowCount() == 1
        assert not Path(config.mappings_file_path).exists()


class TestManualOverridesSection:
    def test_loads_existing_manual_mappings_and_ignore_lists(self, qt_app: QApplication, config: Config) -> None:
        mappings = MappingsConfig(
            manual_mappings=[ManualMapping(anilist_id=1, mal_id=2, comment="note")],
            ignore=IgnoreConfig(anilist_ids=[5], mal_ids=[6], titles=["Foo"]),
        )
        mappings.save(config.mappings_file_path)

        tab = MappingIssuesTab(lambda: config)

        assert tab.manual_table.rowCount() == 1
        assert tab.manual_table.item(0, 0).text() == "1"
        assert tab.manual_table.item(0, 1).text() == "2"
        assert tab.manual_table.item(0, 2).text() == "note"
        assert tab.ignore_table.rowCount() == 3

    def test_save_writes_manual_mapping_added_via_add_row(self, qt_app: QApplication, config: Config) -> None:
        tab = MappingIssuesTab(lambda: config)

        tab._add_manual_row()
        tab.manual_table.item(0, 0).setText("100")
        tab.manual_table.item(0, 1).setText("200")
        tab.manual_table.item(0, 2).setText("manual note")
        tab.save_button.click()

        assert "Saved" in tab.mappings_status_label.text()
        reloaded = load_mappings(config.mappings_file_path)
        assert reloaded.get_manual_mal_id(100) == 200
        assert reloaded.manual_mappings[0].comment == "manual note"

    def test_save_writes_ignore_rows_split_by_type(self, qt_app: QApplication, config: Config) -> None:
        tab = MappingIssuesTab(lambda: config)

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
        tab = MappingIssuesTab(lambda: config)
        tab._add_manual_row()
        tab.manual_table.item(0, 0).setText("not-a-number")

        tab.save_button.click()

        assert "must be numbers" in tab.mappings_status_label.text()
        assert not Path(config.mappings_file_path).exists()

    def test_remove_selected_rows(self, qt_app: QApplication, config: Config) -> None:
        tab = MappingIssuesTab(lambda: config)
        tab._add_manual_row()
        tab._add_manual_row()
        tab.manual_table.selectRow(0)

        tab._remove_selected_rows(tab.manual_table)

        assert tab.manual_table.rowCount() == 1
