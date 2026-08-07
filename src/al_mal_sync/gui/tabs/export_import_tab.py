"""Manual Sync page (labeled "Import / Export" internally -- see
main_window.py's _pages): read or write the standard MAL-format XML list
file (myanimelist.net's own export schema, and the same one AniList's list
importer accepts). Unlike the Auto-Sync page (sync_tab.py), which talks
straight to both APIs, this requires exporting a file and importing it by
hand -- hence "Manual". AniList has no native list-export feature of its
own, so this is the only way to get an AniList list into a file;
MyAnimeList gets the same export/import pair here for parity/scripting.

Export just fetches + serializes (sync.xml_sync.run_export) -- no matching
involved, so it's a single click plus a folder picker. Import reuses the
exact same live matching pipeline the Auto-Sync page uses (sync.xml_sync.
run_import -> the same build_strategy_chain/Updater/service classes
sync.runner.run_sync drives), so its advanced options and result rendering
mirror sync_tab.py's on purpose rather than reinventing either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...sync.statistics import SyncStatistics, format_statistics_table
from ...sync.xml_sync import run_export, run_import
from ..theme import DANGER, SUCCESS
from ..widgets import CollapsibleSection, apply_page_layout, cap_width, field_and_button_row, left_aligned
from ..workers import run_in_thread

_FIELD_WIDTH = 340
_BUTTON_WIDTH = 220

_SERVICE_ITEMS = ("AniList", "MyAnimeList")
_EXPORT_WHAT_ITEMS = ("Anime", "Manga", "Both anime and manga")
_IMPORT_KIND_ITEMS = ("Auto-detect", "Anime", "Manga")


class ExportImportTab(QWidget):
    # Worker-thread progress callback for imports (see sync_tab.py's
    # identical progress_updated signal for why this indirection through a
    # Signal, rather than updating the progress bar directly, is required).
    import_progress_updated = Signal(int, int)

    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._export_thread = None
        self._export_worker = None
        self._export_pending_dir = ""
        self._export_pending_service = ""
        self._import_thread = None
        self._import_worker = None

        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        title = QLabel("Manual Sync", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Read or write a MAL-format XML list file, then import it wherever it needs to "
            "go -- useful since AniList has no list-export feature of its own.",
            self,
        )
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        layout.addWidget(self._build_export_group())
        layout.addWidget(self._build_import_group())
        layout.addStretch(1)

        self.import_progress_updated.connect(self._update_import_progress_bar)

    # -- export --------------------------------------------------------

    def _build_export_group(self) -> QGroupBox:
        group = QGroupBox("Export", self)
        layout = QVBoxLayout(group)

        form = QFormLayout()
        self.export_service_combo = QComboBox(group)
        self.export_service_combo.addItems(_SERVICE_ITEMS)
        self.export_service_combo.setToolTip("Whose list to export.")
        form.addRow("Service", cap_width(self.export_service_combo, _FIELD_WIDTH))

        self.export_what_combo = QComboBox(group)
        self.export_what_combo.addItems(_EXPORT_WHAT_ITEMS)
        form.addRow("Content", cap_width(self.export_what_combo, _FIELD_WIDTH))

        self.export_dir_field = QLineEdit(group)
        self.export_dir_field.setReadOnly(True)
        self.export_dir_field.setPlaceholderText("Choose a folder to save into...")
        browse_button = QPushButton("Browse...", group)
        browse_button.clicked.connect(self._on_browse_export_dir)
        form.addRow("Save to", field_and_button_row(self.export_dir_field, browse_button, _FIELD_WIDTH))
        layout.addLayout(form)

        self.export_button = QPushButton("Export", group)
        self.export_button.setObjectName("primaryButton")
        self.export_button.clicked.connect(self._on_export_clicked)
        layout.addLayout(left_aligned(self.export_button, _BUTTON_WIDTH))

        self.export_status_label = QLabel("", group)
        self.export_status_label.setWordWrap(True)
        layout.addWidget(self.export_status_label)

        return group

    def _on_browse_export_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if path:
            self.export_dir_field.setText(path)

    def _on_export_clicked(self) -> None:
        if self._export_thread is not None:
            return  # an export is already running
        output_dir = self.export_dir_field.text().strip()
        if not output_dir:
            self.export_status_label.setText("Choose a folder to save into first.")
            self.export_status_label.setStyleSheet(f"color: {DANGER};")
            return

        self._export_pending_dir = output_dir
        self._export_pending_service = "anilist" if self.export_service_combo.currentIndex() == 0 else "myanimelist"
        manga = self.export_what_combo.currentIndex() == 1
        all_media = self.export_what_combo.currentIndex() == 2

        self.export_button.setEnabled(False)
        self.export_status_label.setText("Exporting...")
        self.export_status_label.setStyleSheet("")

        self._export_thread, self._export_worker = run_in_thread(
            self,
            run_export,
            self._get_config(),
            service=self._export_pending_service,
            manga=manga,
            all_media=all_media,
            on_finished=self._on_export_finished,
            on_error=self._on_export_error,
        )

    def _on_export_finished(self, result: object) -> None:
        self._export_thread = None
        self._export_worker = None
        self.export_button.setEnabled(True)
        documents: dict[str, str] = result  # type: ignore[assignment]

        written = []
        for kind, xml_text in documents.items():
            path = Path(self._export_pending_dir) / f"{self._export_pending_service}_{kind}.xml"
            try:
                path.write_text(xml_text, encoding="utf-8")
            except OSError as exc:
                self.export_status_label.setText(f"Export failed while writing {path}: {exc}")
                self.export_status_label.setStyleSheet(f"color: {DANGER};")
                return
            written.append(str(path))

        self.export_status_label.setText("Wrote:\n" + "\n".join(written))
        self.export_status_label.setStyleSheet(f"color: {SUCCESS};")

    def _on_export_error(self, message: str) -> None:
        self._export_thread = None
        self._export_worker = None
        self.export_button.setEnabled(True)
        self.export_status_label.setText(f"Export failed: {message}")
        self.export_status_label.setStyleSheet(f"color: {DANGER};")

    # -- import --------------------------------------------------------

    def _build_import_group(self) -> QGroupBox:
        group = QGroupBox("Import", self)
        layout = QVBoxLayout(group)

        form = QFormLayout()
        self.import_file_field = QLineEdit(group)
        self.import_file_field.setReadOnly(True)
        self.import_file_field.setPlaceholderText("Choose a MAL-format XML file...")
        choose_button = QPushButton("Choose File...", group)
        choose_button.clicked.connect(self._on_choose_import_file)
        form.addRow("File", field_and_button_row(self.import_file_field, choose_button, _FIELD_WIDTH))

        self.import_target_combo = QComboBox(group)
        self.import_target_combo.addItems(_SERVICE_ITEMS)
        self.import_target_combo.setToolTip("Which service to import the list into.")
        form.addRow("Import into", cap_width(self.import_target_combo, _FIELD_WIDTH))

        self.import_kind_combo = QComboBox(group)
        self.import_kind_combo.addItems(_IMPORT_KIND_ITEMS)
        self.import_kind_combo.setToolTip("Leave on Auto-detect unless the file's contents are ambiguous.")
        form.addRow("Content", cap_width(self.import_kind_combo, _FIELD_WIDTH))
        layout.addLayout(form)

        advanced_content = self._build_import_advanced_group()
        layout.addWidget(CollapsibleSection("Advanced options", advanced_content, collapsed=True))

        self.import_button = QPushButton("Import", group)
        self.import_button.setObjectName("primaryButton")
        self.import_button.clicked.connect(self._on_import_clicked)
        layout.addLayout(left_aligned(self.import_button, _BUTTON_WIDTH))

        self.import_progress_bar = QProgressBar(group)
        self.import_progress_bar.setRange(0, 1)
        self.import_progress_bar.setVisible(False)
        layout.addWidget(self.import_progress_bar)

        self.import_results_view = QPlainTextEdit(group)
        self.import_results_view.setReadOnly(True)
        self.import_results_view.setVisible(False)
        layout.addWidget(self.import_results_view)

        return group

    def _build_import_advanced_group(self) -> QWidget:
        # A plain widget, not a QGroupBox -- see sync_tab.py's identical
        # advanced-options group for why (the CollapsibleSection wrapping
        # this already supplies the heading/boundary).
        group = QWidget(self)
        form = QFormLayout(group)
        form.setContentsMargins(4, 8, 4, 4)

        self.import_force_checkbox = QCheckBox("Force (skip matching, import by ID directly)", group)
        self.import_force_checkbox.setToolTip(
            "Skip automatic title matching and import entries by ID directly.\n"
            "Only useful for troubleshooting -- leave this off normally."
        )
        self.import_dry_run_checkbox = QCheckBox("Dry run (preview only, no changes made)", group)
        self.import_dry_run_checkbox.setToolTip(
            "Show what would change without actually updating anything."
        )
        self.import_offline_db_checkbox = QCheckBox("Force-enable offline database", group)
        self.import_offline_db_refresh_checkbox = QCheckBox("Force-refresh offline database cache", group)
        self.import_arm_api_checkbox = QCheckBox("Enable ARM API fallback", group)
        self.import_arm_api_url_field = QLineEdit(group)
        self.import_arm_api_url_field.setPlaceholderText("override ARM API base URL (optional)")
        self.import_jikan_api_checkbox = QCheckBox("Enable Jikan API", group)

        for box in (
            self.import_force_checkbox, self.import_dry_run_checkbox,
            self.import_offline_db_checkbox, self.import_offline_db_refresh_checkbox,
            self.import_arm_api_checkbox,
        ):
            form.addRow(box)
        form.addRow("ARM API URL", cap_width(self.import_arm_api_url_field, _FIELD_WIDTH))
        form.addRow(self.import_jikan_api_checkbox)
        return group

    def _on_choose_import_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Choose XML list file", "", "XML files (*.xml)")
        if path:
            self.import_file_field.setText(path)

    def _on_import_clicked(self) -> None:
        if self._import_thread is not None:
            return  # an import is already running
        file_path = self.import_file_field.text().strip()
        if not file_path:
            self._show_import_result("Choose a file to import first.")
            return

        try:
            xml_text = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            self._show_import_result(f"Couldn't read file: {exc}")
            return

        target_service = "anilist" if self.import_target_combo.currentIndex() == 0 else "myanimelist"
        kind_index = self.import_kind_combo.currentIndex()
        kind = None if kind_index == 0 else ("anime" if kind_index == 1 else "manga")

        self.import_button.setEnabled(False)
        self.import_progress_bar.setRange(0, 0)  # indeterminate until the first on_progress call
        self.import_progress_bar.setVisible(True)
        self.import_results_view.clear()
        self.import_results_view.setVisible(False)

        self._import_thread, self._import_worker = run_in_thread(
            self,
            run_import,
            self._get_config(),
            xml_text=xml_text,
            target_service=target_service,
            kind=kind,
            force=self.import_force_checkbox.isChecked(),
            dry_run=self.import_dry_run_checkbox.isChecked(),
            offline_db=self.import_offline_db_checkbox.isChecked(),
            offline_db_force_refresh=self.import_offline_db_refresh_checkbox.isChecked(),
            arm_api=self.import_arm_api_checkbox.isChecked(),
            arm_api_url=self.import_arm_api_url_field.text().strip() or None,
            jikan_api=self.import_jikan_api_checkbox.isChecked(),
            on_progress=self._emit_import_progress,
            on_finished=self._on_import_finished,
            on_error=self._on_import_error,
        )

    def _emit_import_progress(self, current: int, total: int) -> None:
        self.import_progress_updated.emit(current, total)

    def _update_import_progress_bar(self, current: int, total: int) -> None:
        self.import_progress_bar.setRange(0, total)
        self.import_progress_bar.setValue(current)

    def _on_import_finished(self, result: object) -> None:
        kind, outcome = result  # type: ignore[misc]
        self._import_thread = None
        self._import_worker = None
        self.import_button.setEnabled(True)
        self.import_progress_bar.setRange(0, 1)
        self.import_progress_bar.setValue(1)
        self._show_import_result(format_statistics_table(SyncStatistics.from_outcomes({kind: outcome})))

    def _on_import_error(self, message: str) -> None:
        self._import_thread = None
        self._import_worker = None
        self.import_button.setEnabled(True)
        self.import_progress_bar.setRange(0, 1)
        self.import_progress_bar.setValue(0)
        self._show_import_result(f"Import failed: {message}")

    def _show_import_result(self, text: str) -> None:
        self.import_results_view.setPlainText(text)
        self.import_results_view.setVisible(True)
