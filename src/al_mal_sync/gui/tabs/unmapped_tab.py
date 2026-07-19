"""Unmapped tab: table of entries the last sync couldn't match, with
actions that replicate exactly what cli.py's `unmapped --fix` loop does --
the same MappingsConfig/UnmappedState primitives, saved the same way, just
clicked instead of typed at a prompt.

Actions are persistent buttons below the table operating on the selected
row (single selection), the same pattern mappings_tab.py's "Remove
Selected" already uses -- not a QPushButton trio rebuilt as a per-row cell
widget on every render, which turned out to have a real PySide6 widget
lifecycle hazard: shrinking a QTableWidget that has custom cell widgets
sitting in it can leave a dangling widget wrapper that crashes much later,
whenever Python's GC finally gets to it (reproduced via the test suite,
not something a user would necessarily hit right away).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...mapping.manual_mappings import MappingsConfig, load_mappings
from ...unmapped import UnmappedRecord, UnmappedState, load_unmapped_state, save_unmapped_state

_COLUMNS = ("Title", "Media", "Direction", "AniList ID", "MAL ID", "Reason")


class UnmappedTab(QWidget):
    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._state = UnmappedState()

        layout = QVBoxLayout(self)

        self.table = QTableWidget(self)
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.reload)
        buttons.addWidget(self.refresh_button)
        self.ignore_id_button = QPushButton("Ignore Selected (ID)", self)
        self.ignore_id_button.clicked.connect(self._on_ignore_selected_by_id)
        buttons.addWidget(self.ignore_id_button)
        self.ignore_title_button = QPushButton("Ignore Selected (Title)", self)
        self.ignore_title_button.clicked.connect(self._on_ignore_selected_by_title)
        buttons.addWidget(self.ignore_title_button)
        self.map_button = QPushButton("Map Selected to MAL ID...", self)
        self.map_button.clicked.connect(self._on_map_selected)
        buttons.addWidget(self.map_button)
        self.ignore_all_button = QPushButton("Ignore All", self)
        self.ignore_all_button.clicked.connect(self._on_ignore_all)
        buttons.addWidget(self.ignore_all_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.reload()

    def reload(self) -> None:
        config = self._get_config()
        self._state = load_unmapped_state(config.resolved_unmapped_state_path)
        self._render_table()

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._state.entries))
        for row, entry in enumerate(self._state.entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.title))
            self.table.setItem(row, 1, QTableWidgetItem(entry.media_type))
            self.table.setItem(row, 2, QTableWidgetItem(entry.direction))
            self.table.setItem(row, 3, QTableWidgetItem(str(entry.anilist_id or "-")))
            self.table.setItem(row, 4, QTableWidgetItem(str(entry.mal_id or "-")))
            self.table.setItem(row, 5, QTableWidgetItem(entry.reason))

    def _selected_entry(self) -> UnmappedRecord | None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        row = rows.pop()
        if row >= len(self._state.entries):
            return None
        return self._state.entries[row]

    def _load_mappings(self) -> MappingsConfig:
        return load_mappings(self._get_config().resolved_mappings_file_path)

    def _save(self, mappings: MappingsConfig) -> None:
        config = self._get_config()
        mappings.save(config.resolved_mappings_file_path)
        save_unmapped_state(self._state, config.resolved_unmapped_state_path)
        self._render_table()

    def _ignore_by_id(self, entry: UnmappedRecord) -> None:
        mappings = self._load_mappings()
        if entry.anilist_id > 0:
            mappings.add_ignore_by_id(entry.anilist_id)
        elif entry.mal_id > 0:
            mappings.add_ignore_by_mal_id(entry.mal_id)
        if entry in self._state.entries:
            self._state.entries.remove(entry)
        self._save(mappings)

    def _ignore_by_title(self, entry: UnmappedRecord) -> None:
        mappings = self._load_mappings()
        mappings.ignore.titles.append(entry.title)
        self._state.remove_by_title(entry.title)
        self._save(mappings)

    def _map_manually(self, entry: UnmappedRecord) -> None:
        mal_id, ok = QInputDialog.getInt(
            self, "Map to MAL ID", f"MAL id for {entry.title!r}:", 1, 1
        )
        if not ok:
            return
        anilist_id = entry.anilist_id
        if anilist_id <= 0:
            anilist_id, ok = QInputDialog.getInt(
                self, "AniList ID", f"AniList id for {entry.title!r}:", 1, 1
            )
            if not ok:
                return

        mappings = self._load_mappings()
        mappings.add_manual_mapping(anilist_id, mal_id)
        if entry in self._state.entries:
            self._state.entries.remove(entry)
        self._save(mappings)

    def _on_ignore_selected_by_id(self) -> None:
        entry = self._selected_entry()
        if entry is not None and (entry.anilist_id > 0 or entry.mal_id > 0):
            self._ignore_by_id(entry)

    def _on_ignore_selected_by_title(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._ignore_by_title(entry)

    def _on_map_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._map_manually(entry)

    def _on_ignore_all(self) -> None:
        if not self._state.entries:
            return
        mappings = self._load_mappings()
        for entry in self._state.entries:
            if entry.anilist_id > 0:
                mappings.add_ignore_by_id(entry.anilist_id)
            elif entry.mal_id > 0:
                mappings.add_ignore_by_mal_id(entry.mal_id)
        self._state.clear()
        self._save(mappings)
