"""Tests for gui/tabs/unmapped_tab.py: table rendering, the
selection-based action buttons, and that each action calls the exact same
MappingsConfig/UnmappedState primitives cli.py's `unmapped --fix` loop
does. mappings_file_path and the unmapped state path always point at
tmp_path -- never real user files."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs.unmapped_tab import UnmappedTab  # noqa: E402
from al_mal_sync.mapping.manual_mappings import load_mappings  # noqa: E402
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


class TestUnmappedTab:
    def test_reload_renders_entries(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry())

        tab = UnmappedTab(lambda: config)

        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 0).text() == "Some Show"

    def test_ignore_by_id_removes_row_and_updates_mappings(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=42))
        tab = UnmappedTab(lambda: config)

        tab._ignore_by_id(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert 42 in mappings.ignore.anilist_ids

    def test_ignore_by_title_removes_row_and_updates_mappings(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(title="Weird Title", anilist_id=0))
        tab = UnmappedTab(lambda: config)

        tab._ignore_by_title(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert "Weird Title" in mappings.ignore.titles

    def test_map_manually_adds_manual_mapping_and_removes_row(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(config, _entry(anilist_id=7))
        tab = UnmappedTab(lambda: config)

        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.unmapped_tab.QInputDialog.getInt",
            lambda *a, **kw: (999, True),
        )
        tab._map_manually(tab._state.entries[0])

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert mappings.get_manual_mal_id(7) == 999

    def test_ignore_all_clears_every_row(self, qt_app: QApplication, config: Config) -> None:
        _write_state(config, _entry(anilist_id=1), _entry(anilist_id=2, title="Other"))
        tab = UnmappedTab(lambda: config)

        tab._on_ignore_all()

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert set(mappings.ignore.anilist_ids) == {1, 2}

    def test_no_selection_ignore_button_click_is_a_no_op(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=1))
        tab = UnmappedTab(lambda: config)

        tab.ignore_id_button.click()  # nothing selected

        assert tab.table.rowCount() == 1
        assert not Path(config.mappings_file_path).exists()

    def test_selecting_row_then_clicking_ignore_id_button_removes_it(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=1), _entry(anilist_id=2, title="Other"))
        tab = UnmappedTab(lambda: config)
        tab.table.selectRow(0)

        tab.ignore_id_button.click()

        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 0).text() == "Other"
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert mappings.ignore.anilist_ids == [1]

    def test_selecting_row_then_clicking_ignore_title_button_removes_it(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(title="Weird Title", anilist_id=0))
        tab = UnmappedTab(lambda: config)
        tab.table.selectRow(0)

        tab.ignore_title_button.click()

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert "Weird Title" in mappings.ignore.titles

    def test_selecting_row_then_clicking_map_button_prompts_and_saves(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(config, _entry(anilist_id=7))
        tab = UnmappedTab(lambda: config)
        tab.table.selectRow(0)

        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.unmapped_tab.QInputDialog.getInt",
            lambda *a, **kw: (999, True),
        )
        tab.map_button.click()

        assert tab.table.rowCount() == 0
        mappings = load_mappings(config.resolved_mappings_file_path)
        assert mappings.get_manual_mal_id(7) == 999

    def test_ignore_id_button_disabled_action_skipped_when_entry_has_no_ids(
        self, qt_app: QApplication, config: Config
    ) -> None:
        _write_state(config, _entry(anilist_id=0, mal_id=0))
        tab = UnmappedTab(lambda: config)
        tab.table.selectRow(0)

        tab.ignore_id_button.click()

        # No id to ignore by -- row stays, nothing written.
        assert tab.table.rowCount() == 1
        assert not Path(config.mappings_file_path).exists()
