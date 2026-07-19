"""Mappings tab: table editor over mappings.yaml's manual_mappings and its
three ignore lists (anilist_ids/mal_ids/titles), using the same
MappingsConfig load/save the CLI and Unmapped tab already use -- a full
substitute for hand-editing mappings.yaml.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...mapping.manual_mappings import ManualMapping, MappingsConfig, load_mappings

_IGNORE_TYPES = ("AniList ID", "MAL ID", "Title")


class MappingsTab(QWidget):
    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._mappings = MappingsConfig()

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Manual Mappings (AniList ID <-> MAL ID)", self))
        self.manual_table = QTableWidget(self)
        self.manual_table.setColumnCount(3)
        self.manual_table.setHorizontalHeaderLabels(["AniList ID", "MAL ID", "Comment"])
        self.manual_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.manual_table)

        manual_buttons = QHBoxLayout()
        add_manual_button = QPushButton("Add Row", self)
        add_manual_button.clicked.connect(self._add_manual_row)
        manual_buttons.addWidget(add_manual_button)
        remove_manual_button = QPushButton("Remove Selected", self)
        remove_manual_button.clicked.connect(lambda: self._remove_selected_rows(self.manual_table))
        manual_buttons.addWidget(remove_manual_button)
        manual_buttons.addStretch(1)
        layout.addLayout(manual_buttons)

        layout.addWidget(QLabel("Ignore List", self))
        self.ignore_table = QTableWidget(self)
        self.ignore_table.setColumnCount(2)
        self.ignore_table.setHorizontalHeaderLabels(["Type", "Value"])
        self.ignore_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.ignore_table)

        ignore_buttons = QHBoxLayout()
        add_ignore_button = QPushButton("Add Row", self)
        add_ignore_button.clicked.connect(self._add_ignore_row)
        ignore_buttons.addWidget(add_ignore_button)
        remove_ignore_button = QPushButton("Remove Selected", self)
        remove_ignore_button.clicked.connect(lambda: self._remove_selected_rows(self.ignore_table))
        ignore_buttons.addWidget(remove_ignore_button)
        ignore_buttons.addStretch(1)
        layout.addLayout(ignore_buttons)

        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save", self)
        self.save_button.clicked.connect(self._on_save)
        save_row.addWidget(self.save_button)
        self.status_label = QLabel("", self)
        save_row.addWidget(self.status_label)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        self.reload()

    def reload(self) -> None:
        config = self._get_config()
        self._mappings = load_mappings(config.resolved_mappings_file_path)
        self._render_manual_table()
        self._render_ignore_table()
        self.status_label.setText("")

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

        # Clear to zero rows first -- see unmapped_tab.py's _render_table
        # for why resizing a table with per-row cell widgets (the type
        # QComboBox here) in place is unsafe.
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
                self.status_label.setText(f"Row {row + 1}: AniList ID and MAL ID must be numbers")
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
                    self.status_label.setText(f"Row {row + 1}: AniList ID must be a number")
                    return
            elif type_ == "MAL ID":
                try:
                    mal_ids.append(int(value))
                except ValueError:
                    self.status_label.setText(f"Row {row + 1}: MAL ID must be a number")
                    return
            else:
                titles.append(value)

        self._mappings.manual_mappings = manual_mappings
        self._mappings.ignore.anilist_ids = anilist_ids
        self._mappings.ignore.mal_ids = mal_ids
        self._mappings.ignore.titles = titles

        config = self._get_config()
        self._mappings.save(config.resolved_mappings_file_path)
        self.status_label.setText("Saved.")
