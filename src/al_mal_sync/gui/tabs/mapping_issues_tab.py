"""Mapping Issues page: merges the old separate Unmapped and Mappings pages
into one, since both are really "things the automatic title matching needs
help with" -- having them as two unrelated tabs made neither easy to find.

"Needs your attention" (top, always visible) is entries the last sync
couldn't match, with actions that replicate exactly what cli.py's `unmapped
--fix` loop does -- the same MappingsConfig/UnmappedState primitives, saved
the same way, just clicked instead of typed at a prompt.

"Manual overrides" (bottom, collapsed by default -- this is the advanced,
rarely-needed half) is a table editor over mappings.yaml's manual_mappings
and its three ignore lists, using the same MappingsConfig load/save the CLI
and the "needs attention" actions above already use.

Actions on the unmapped table are persistent buttons below it operating on
the selected row (single selection), not a QPushButton trio rebuilt as a
per-row cell widget on every render -- that turned out to have a real
PySide6 widget lifecycle hazard: shrinking a QTableWidget that has custom
cell widgets sitting in it can leave a dangling widget wrapper that crashes
much later, whenever Python's GC finally gets to it (reproduced via the test
suite, not something a user would necessarily hit right away).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...mapping.manual_mappings import ManualMapping, MappingsConfig, load_mappings
from ...unmapped import UnmappedRecord, UnmappedState, load_unmapped_state, save_unmapped_state
from ..widgets import CollapsibleSection, apply_page_layout

_COLUMNS = ("Title", "Media", "Direction", "AniList ID", "MAL ID", "Reason")
_DIRECTION_LABELS = {"forward": "AniList -> MyAnimeList", "reverse": "MyAnimeList -> AniList"}
_IGNORE_TYPES = ("AniList ID", "MAL ID", "Title")


def _direction_label(direction: str) -> str:
    return _DIRECTION_LABELS.get(direction, direction)


class MappingIssuesTab(QWidget):
    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._state = UnmappedState()
        self._mappings = MappingsConfig()

        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        title = QLabel("Mapping Issues", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Titles the last sync couldn't automatically match between AniList and MyAnimeList.", self
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_needs_attention_group())
        layout.addWidget(CollapsibleSection("Manual overrides", self._build_manual_overrides_group()))

        self.reload()

    # -- "Needs your attention" (unmapped entries) ------------------------

    def _build_needs_attention_group(self) -> QGroupBox:
        group = QGroupBox("Needs your attention", self)
        group_layout = QVBoxLayout(group)

        self.table = QTableWidget(group)
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        group_layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh", group)
        self.refresh_button.clicked.connect(self.reload)
        buttons.addWidget(self.refresh_button)
        self.ignore_id_button = QPushButton("Ignore Selected", group)
        self.ignore_id_button.setToolTip("Never ask about this exact title again (matched by its ID).")
        self.ignore_id_button.clicked.connect(self._on_ignore_selected_by_id)
        buttons.addWidget(self.ignore_id_button)
        self.ignore_title_button = QPushButton("Ignore Selected (by title)", group)
        self.ignore_title_button.setToolTip(
            "Never ask about this title again, matched by its name instead of an ID."
        )
        self.ignore_title_button.clicked.connect(self._on_ignore_selected_by_title)
        buttons.addWidget(self.ignore_title_button)
        self.map_button = QPushButton("Map to MyAnimeList ID...", group)
        self.map_button.setToolTip("Manually tell it which MyAnimeList entry this title matches.")
        self.map_button.clicked.connect(self._on_map_selected)
        buttons.addWidget(self.map_button)
        self.ignore_all_button = QPushButton("Ignore All", group)
        self.ignore_all_button.setObjectName("dangerButton")
        self.ignore_all_button.clicked.connect(self._on_ignore_all)
        buttons.addWidget(self.ignore_all_button)
        buttons.addStretch(1)
        group_layout.addLayout(buttons)

        return group

    def reload(self) -> None:
        config = self._get_config()
        self._state = load_unmapped_state(config.resolved_unmapped_state_path)
        self._render_table()
        self._mappings = load_mappings(config.resolved_mappings_file_path)
        self._render_manual_table()
        self._render_ignore_table()
        self.mappings_status_label.setText("")

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._state.entries))
        for row, entry in enumerate(self._state.entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.title))
            self.table.setItem(row, 1, QTableWidgetItem(entry.media_type))
            self.table.setItem(row, 2, QTableWidgetItem(_direction_label(entry.direction)))
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

    def _save_unmapped(self, mappings: MappingsConfig) -> None:
        config = self._get_config()
        mappings.save(config.resolved_mappings_file_path)
        save_unmapped_state(self._state, config.resolved_unmapped_state_path)
        self._mappings = mappings
        self._render_table()
        self._render_manual_table()
        self._render_ignore_table()

    def _ignore_by_id(self, entry: UnmappedRecord) -> None:
        mappings = load_mappings(self._get_config().resolved_mappings_file_path)
        if entry.anilist_id > 0:
            mappings.add_ignore_by_id(entry.anilist_id)
        elif entry.mal_id > 0:
            mappings.add_ignore_by_mal_id(entry.mal_id)
        if entry in self._state.entries:
            self._state.entries.remove(entry)
        self._save_unmapped(mappings)

    def _ignore_by_title(self, entry: UnmappedRecord) -> None:
        mappings = load_mappings(self._get_config().resolved_mappings_file_path)
        mappings.ignore.titles.append(entry.title)
        self._state.remove_by_title(entry.title)
        self._save_unmapped(mappings)

    def _map_manually(self, entry: UnmappedRecord) -> None:
        mal_id, ok = QInputDialog.getInt(
            self, "Map to MAL ID", f"MyAnimeList id for {entry.title!r}:", 1, 1
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

        mappings = load_mappings(self._get_config().resolved_mappings_file_path)
        mappings.add_manual_mapping(anilist_id, mal_id)
        if entry in self._state.entries:
            self._state.entries.remove(entry)
        self._save_unmapped(mappings)

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
        mappings = load_mappings(self._get_config().resolved_mappings_file_path)
        for entry in self._state.entries:
            if entry.anilist_id > 0:
                mappings.add_ignore_by_id(entry.anilist_id)
            elif entry.mal_id > 0:
                mappings.add_ignore_by_mal_id(entry.mal_id)
        self._state.clear()
        self._save_unmapped(mappings)

    # -- "Manual overrides" (mappings.yaml editor) -------------------------

    def _build_manual_overrides_group(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)

        layout.addWidget(QLabel("Manual Mappings (AniList ID <-> MAL ID)", container))
        self.manual_table = QTableWidget(container)
        self.manual_table.setColumnCount(3)
        self.manual_table.setHorizontalHeaderLabels(["AniList ID", "MAL ID", "Comment"])
        self.manual_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.manual_table)

        manual_buttons = QHBoxLayout()
        add_manual_button = QPushButton("Add Row", container)
        add_manual_button.clicked.connect(self._add_manual_row)
        manual_buttons.addWidget(add_manual_button)
        remove_manual_button = QPushButton("Remove Selected", container)
        remove_manual_button.clicked.connect(lambda: self._remove_selected_rows(self.manual_table))
        manual_buttons.addWidget(remove_manual_button)
        manual_buttons.addStretch(1)
        layout.addLayout(manual_buttons)

        layout.addWidget(QLabel("Ignore List", container))
        self.ignore_table = QTableWidget(container)
        self.ignore_table.setColumnCount(2)
        self.ignore_table.setHorizontalHeaderLabels(["Type", "Value"])
        self.ignore_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.ignore_table)

        ignore_buttons = QHBoxLayout()
        add_ignore_button = QPushButton("Add Row", container)
        add_ignore_button.clicked.connect(self._add_ignore_row)
        ignore_buttons.addWidget(add_ignore_button)
        remove_ignore_button = QPushButton("Remove Selected", container)
        remove_ignore_button.clicked.connect(lambda: self._remove_selected_rows(self.ignore_table))
        ignore_buttons.addWidget(remove_ignore_button)
        ignore_buttons.addStretch(1)
        layout.addLayout(ignore_buttons)

        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save", container)
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._on_save)
        save_row.addWidget(self.save_button)
        self.mappings_status_label = QLabel("", container)
        save_row.addWidget(self.mappings_status_label)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        return container

    def _render_manual_table(self) -> None:
        self.manual_table.setRowCount(len(self._mappings.manual_mappings))
        for row, mapping in enumerate(self._mappings.manual_mappings):
            self.manual_table.setItem(row, 0, QTableWidgetItem(str(mapping.anilist_id)))
            self.manual_table.setItem(row, 1, QTableWidgetItem(str(mapping.mal_id)))
            self.manual_table.setItem(row, 2, QTableWidgetItem(mapping.comment))

    def _render_ignore_table(self) -> None:
        rows: list[tuple[str, str]] = []
        rows.extend(("AniList ID", str(v)) for v in self._mappings.ignore.anilist_ids)
        rows.extend(("MAL ID", str(v)) for v in self._mappings.ignore.mal_ids)
        rows.extend(("Title", v) for v in self._mappings.ignore.titles)

        # Clear to zero rows first -- see the unmapped table's _render_table
        # docstring note above for why resizing a table with per-row cell
        # widgets (the type QComboBox here) in place is unsafe.
        self.ignore_table.setRowCount(0)
        self.ignore_table.setRowCount(len(rows))
        for row, (type_, value) in enumerate(rows):
            self._set_ignore_type_combo(row, type_)
            self.ignore_table.setItem(row, 1, QTableWidgetItem(value))

    def _set_ignore_type_combo(self, row: int, current: str = "Title") -> QComboBox:
        combo = QComboBox(self.ignore_table)
        combo.addItems(_IGNORE_TYPES)
        combo.setCurrentText(current)
        self.ignore_table.setCellWidget(row, 0, combo)
        return combo

    def _add_manual_row(self) -> None:
        row = self.manual_table.rowCount()
        self.manual_table.insertRow(row)
        self.manual_table.setItem(row, 0, QTableWidgetItem("0"))
        self.manual_table.setItem(row, 1, QTableWidgetItem("0"))
        self.manual_table.setItem(row, 2, QTableWidgetItem(""))

    def _add_ignore_row(self) -> None:
        row = self.ignore_table.rowCount()
        self.ignore_table.insertRow(row)
        self._set_ignore_type_combo(row)
        self.ignore_table.setItem(row, 1, QTableWidgetItem(""))

    def _remove_selected_rows(self, table: QTableWidget) -> None:
        rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        for row in rows:
            table.removeRow(row)

    def _on_save(self) -> None:
        manual_mappings = []
        for row in range(self.manual_table.rowCount()):
            anilist_item = self.manual_table.item(row, 0)
            mal_item = self.manual_table.item(row, 1)
            comment_item = self.manual_table.item(row, 2)
            try:
                anilist_id = int(anilist_item.text()) if anilist_item else 0
                mal_id = int(mal_item.text()) if mal_item else 0
            except ValueError:
                self.mappings_status_label.setText(f"Row {row + 1}: AniList ID and MAL ID must be numbers")
                return
            comment = comment_item.text() if comment_item else ""
            manual_mappings.append(ManualMapping(anilist_id, mal_id, comment))

        anilist_ids: list[int] = []
        mal_ids: list[int] = []
        titles: list[str] = []
        for row in range(self.ignore_table.rowCount()):
            combo = self.ignore_table.cellWidget(row, 0)
            value_item = self.ignore_table.item(row, 1)
            value = value_item.text().strip() if value_item else ""
            if not value:
                continue
            type_ = combo.currentText() if isinstance(combo, QComboBox) else "Title"
            if type_ == "AniList ID":
                try:
                    anilist_ids.append(int(value))
                except ValueError:
                    self.mappings_status_label.setText(f"Row {row + 1}: AniList ID must be a number")
                    return
            elif type_ == "MAL ID":
                try:
                    mal_ids.append(int(value))
                except ValueError:
                    self.mappings_status_label.setText(f"Row {row + 1}: MAL ID must be a number")
                    return
            else:
                titles.append(value)

        self._mappings.manual_mappings = manual_mappings
        self._mappings.ignore.anilist_ids = anilist_ids
        self._mappings.ignore.mal_ids = mal_ids
        self._mappings.ignore.titles = titles

        config = self._get_config()
        self._mappings.save(config.resolved_mappings_file_path)
        self.mappings_status_label.setText("Saved.")
