"""Tests for the anime-offline-database loader: URL parsing, indexing, and the
download/cache/auto-update decision logic (network calls are monkeypatched out)."""

from __future__ import annotations

from pathlib import Path

import pytest

from al_mal_sync.mapping import offline_database as aod
from al_mal_sync.mapping.offline_database import (
    AODEntry,
    OfflineDatabase,
    OfflineDatabaseError,
    _extract_id_from_url,
    load_offline_database,
)

MAL_PREFIX = "https://myanimelist.net/anime/"
ANILIST_PREFIX = "https://anilist.co/anime/"


class TestExtractIdFromUrl:
    def test_extracts_plain_id(self) -> None:
        assert _extract_id_from_url(f"{MAL_PREFIX}1535", MAL_PREFIX) == 1535

    def test_extracts_id_with_trailing_path(self) -> None:
        assert _extract_id_from_url(f"{MAL_PREFIX}1535/death-note", MAL_PREFIX) == 1535

    def test_wrong_prefix_returns_none(self) -> None:
        assert _extract_id_from_url(f"{ANILIST_PREFIX}1535", MAL_PREFIX) is None

    def test_non_numeric_id_returns_none(self) -> None:
        assert _extract_id_from_url(f"{MAL_PREFIX}abc", MAL_PREFIX) is None

    def test_zero_id_returns_none(self) -> None:
        assert _extract_id_from_url(f"{MAL_PREFIX}0", MAL_PREFIX) is None


class TestOfflineDatabaseIndexing:
    def test_builds_bidirectional_mapping(self) -> None:
        db = OfflineDatabase.build_from_entries(
            [AODEntry(sources=[f"{MAL_PREFIX}1", f"{ANILIST_PREFIX}100"])]
        )
        assert db.get_anilist_id(1) == 100
        assert db.get_mal_id(100) == 1
        assert db.entries == 1

    def test_entry_with_only_one_id_is_not_indexed(self) -> None:
        db = OfflineDatabase.build_from_entries([AODEntry(sources=[f"{MAL_PREFIX}1"])])
        assert db.get_anilist_id(1) is None
        assert db.entries == 0


class TestLoadOfflineDatabase:
    def test_downloads_when_cache_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"download": 0}

        monkeypatch.setattr(
            aod, "_get_latest_release_info", lambda timeout: ("http://x/db.json", "v1")
        )

        def fake_download(url: str, dest_path: Path, timeout: float) -> None:
            calls["download"] += 1
            dest_path.write_text('{"lastUpdate": "2024-01-01", "data": []}', encoding="utf-8")

        monkeypatch.setattr(aod, "_download_file", fake_download)

        db = load_offline_database(str(tmp_path))

        assert calls["download"] == 1
        assert db.last_update == "2024-01-01"
        assert (tmp_path / aod.METADATA_FILE).read_text(encoding="utf-8") == "v1"

    def test_missing_cache_and_failed_download_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(timeout: float) -> tuple[str, str]:
            raise RuntimeError("network down")

        monkeypatch.setattr(aod, "_get_latest_release_info", fail)

        with pytest.raises(OfflineDatabaseError):
            load_offline_database(str(tmp_path))

    def test_existing_cache_survives_failed_refresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / aod.DATABASE_FILE).write_text(
            '{"lastUpdate": "cached", "data": []}', encoding="utf-8"
        )

        def fail(timeout: float) -> tuple[str, str]:
            raise RuntimeError("network down")

        monkeypatch.setattr(aod, "_get_latest_release_info", fail)

        db = load_offline_database(str(tmp_path), force_refresh=True)

        assert db.last_update == "cached"

    def test_auto_update_skips_download_when_version_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / aod.DATABASE_FILE).write_text(
            '{"lastUpdate": "cached", "data": []}', encoding="utf-8"
        )
        (tmp_path / aod.METADATA_FILE).write_text("v1", encoding="utf-8")

        calls = {"download": 0}
        monkeypatch.setattr(
            aod, "_get_latest_release_info", lambda timeout: ("http://x/db.json", "v1")
        )

        def fake_download(url: str, dest_path: Path, timeout: float) -> None:
            calls["download"] += 1

        monkeypatch.setattr(aod, "_download_file", fake_download)

        load_offline_database(str(tmp_path), auto_update=True)

        assert calls["download"] == 0

    def test_auto_update_downloads_when_version_differs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = tmp_path / aod.DATABASE_FILE
        meta_path = tmp_path / aod.METADATA_FILE
        db_path.write_text('{"lastUpdate": "old", "data": []}', encoding="utf-8")
        meta_path.write_text("v1", encoding="utf-8")

        monkeypatch.setattr(
            aod, "_get_latest_release_info", lambda timeout: ("http://x/db.json", "v2")
        )

        def fake_download(url: str, dest_path: Path, timeout: float) -> None:
            dest_path.write_text('{"lastUpdate": "new", "data": []}', encoding="utf-8")

        monkeypatch.setattr(aod, "_download_file", fake_download)

        db = load_offline_database(str(tmp_path), auto_update=True)

        assert db.last_update == "new"
        assert meta_path.read_text(encoding="utf-8") == "v2"

    def test_auto_update_disabled_skips_version_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / aod.DATABASE_FILE).write_text(
            '{"lastUpdate": "cached", "data": []}', encoding="utf-8"
        )

        def fail(*args: object, **kwargs: object) -> None:
            raise AssertionError("should not be called when auto_update=False")

        monkeypatch.setattr(aod, "_get_latest_release_info", fail)

        db = load_offline_database(str(tmp_path), auto_update=False)

        assert db.last_update == "cached"
