"""Tests for gui/tabs/export_import_tab.py: button state while an export/
import is in flight, progress-bar updates arriving safely on the GUI thread,
disk writes on export, and rendering of finished/error outcomes. run_export/
run_import are monkeypatched at the export_import_tab module level -- these
tests exercise the tab's own wiring, not the real XML/sync pipeline (that's
xml_list.py's and xml_sync.py's job, covered elsewhere)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs import export_import_tab as export_import_tab_module  # noqa: E402
from al_mal_sync.gui.tabs.export_import_tab import ExportImportTab  # noqa: E402
from al_mal_sync.sync.updater import SyncOutcome  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.


class TestExport:
    def test_export_button_disabled_while_in_flight(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()

        def blocking_run_export(config: Config, **kwargs: object) -> dict[str, str]:
            release.wait(timeout=5)
            return {}

        monkeypatch.setattr(export_import_tab_module, "run_export", blocking_run_export)
        tab = ExportImportTab(lambda: Config())
        tab.export_dir_field.setText(str(tmp_path))

        try:
            tab.export_button.click()
            assert tab.export_button.isEnabled() is False
        finally:
            release.set()
            wait_until(qt_app, lambda: tab._export_thread is None)

    def test_export_requires_a_directory_first(self, qt_app: QApplication) -> None:
        tab = ExportImportTab(lambda: Config())

        tab.export_button.click()

        assert tab._export_thread is None
        assert "folder" in tab.export_status_label.text().lower()

    def test_export_writes_one_file_per_kind(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            export_import_tab_module, "run_export",
            lambda config, **kw: {"anime": "<anime-xml/>", "manga": "<manga-xml/>"},
        )
        tab = ExportImportTab(lambda: Config())
        tab.export_dir_field.setText(str(tmp_path))
        tab.export_what_combo.setCurrentIndex(2)  # both

        tab.export_button.click()
        wait_until(qt_app, lambda: tab._export_thread is None)

        assert (tmp_path / "anilist_anime.xml").read_text() == "<anime-xml/>"
        assert (tmp_path / "anilist_manga.xml").read_text() == "<manga-xml/>"
        assert "wrote" in tab.export_status_label.text().lower()

    def test_export_error_is_shown(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_run_export(config: Config, **kwargs: object) -> dict[str, str]:
            raise RuntimeError("boom")

        monkeypatch.setattr(export_import_tab_module, "run_export", failing_run_export)
        tab = ExportImportTab(lambda: Config())
        tab.export_dir_field.setText(str(tmp_path))

        tab.export_button.click()
        wait_until(qt_app, lambda: tab._export_thread is None)

        assert "boom" in tab.export_status_label.text()
        assert tab.export_button.isEnabled() is True


class TestImport:
    def test_import_requires_a_file_first(self, qt_app: QApplication) -> None:
        tab = ExportImportTab(lambda: Config())

        tab.import_button.click()

        assert tab._import_thread is None
        assert "choose a file" in tab.import_results_view.toPlainText().lower()

    def test_import_button_disabled_while_in_flight(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()

        def blocking_run_import(config: Config, **kwargs: object) -> tuple[str, SyncOutcome]:
            release.wait(timeout=5)
            return "anime", SyncOutcome()

        monkeypatch.setattr(export_import_tab_module, "run_import", blocking_run_import)
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")
        tab = ExportImportTab(lambda: Config())
        tab.import_file_field.setText(str(xml_file))

        try:
            tab.import_button.click()
            assert tab.import_button.isEnabled() is False
        finally:
            release.set()
            wait_until(qt_app, lambda: tab._import_thread is None)

    def test_import_forwards_selected_options(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_import(config: Config, **kwargs: object) -> tuple[str, SyncOutcome]:
            captured.update(kwargs)
            return "manga", SyncOutcome()

        monkeypatch.setattr(export_import_tab_module, "run_import", fake_run_import)
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")
        tab = ExportImportTab(lambda: Config())
        tab.import_file_field.setText(str(xml_file))
        tab.import_target_combo.setCurrentIndex(1)  # MyAnimeList
        tab.import_kind_combo.setCurrentIndex(2)  # Manga
        tab.import_force_checkbox.setChecked(True)

        tab.import_button.click()
        wait_until(qt_app, lambda: tab._import_thread is None)

        assert captured["target_service"] == "myanimelist"
        assert captured["kind"] == "manga"
        assert captured["xml_text"] == "<myanimelist/>"
        assert captured["force"] is True

    def test_import_finished_renders_statistics(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            export_import_tab_module, "run_import",
            lambda config, **kw: ("anime", SyncOutcome()),
        )
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")
        tab = ExportImportTab(lambda: Config())
        tab.import_file_field.setText(str(xml_file))

        tab.import_button.click()
        wait_until(qt_app, lambda: tab._import_thread is None)

        assert tab.import_results_view.isHidden() is False
        assert tab.import_button.isEnabled() is True

    def test_import_error_is_shown(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_run_import(config: Config, **kwargs: object) -> tuple[str, SyncOutcome]:
            raise RuntimeError("boom")

        monkeypatch.setattr(export_import_tab_module, "run_import", failing_run_import)
        xml_file = tmp_path / "list.xml"
        xml_file.write_text("<myanimelist/>", encoding="utf-8")
        tab = ExportImportTab(lambda: Config())
        tab.import_file_field.setText(str(xml_file))

        tab.import_button.click()
        wait_until(qt_app, lambda: tab._import_thread is None)

        assert "boom" in tab.import_results_view.toPlainText()
        assert tab.import_button.isEnabled() is True
