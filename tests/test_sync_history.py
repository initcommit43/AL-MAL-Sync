"""Tests for sync_history.py: save/load round-trip (atomic write), missing
and corrupt file handling, and the SyncStatistics -> entry converter."""

from __future__ import annotations

from pathlib import Path

from al_mal_sync.sync.statistics import MediaTypeStats, SyncStatistics
from al_mal_sync.sync_history import (
    SyncHistoryEntry,
    load_last_sync,
    save_sync_history,
)


class TestSaveLoadRoundtrip:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_last_sync(tmp_path / "nonexistent.json") is None

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "sync_history.json"
        entry = SyncHistoryEntry(
            finished_at="2026-01-01T00:00:00+00:00",
            per_kind={"anime": {"updated": 3, "skipped": 1, "dry_run": 0, "errors": 0, "unmatched": 2}},
        )

        save_sync_history(entry, path)
        loaded = load_last_sync(path)

        assert loaded is not None
        assert loaded.finished_at == "2026-01-01T00:00:00+00:00"
        assert loaded.per_kind["anime"]["updated"] == 3
        assert loaded.per_kind["anime"]["unmatched"] == 2

    def test_malformed_json_falls_back_to_none(self, tmp_path: Path) -> None:
        path = tmp_path / "sync_history.json"
        path.write_text("not json", encoding="utf-8")
        assert load_last_sync(path) is None

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "state" / "sync_history.json"
        save_sync_history(SyncHistoryEntry(finished_at="2026-01-01T00:00:00+00:00"), path)
        assert path.exists()

    def test_save_overwrites_previous_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "sync_history.json"
        save_sync_history(SyncHistoryEntry(finished_at="2026-01-01T00:00:00+00:00"), path)
        save_sync_history(SyncHistoryEntry(finished_at="2026-01-02T00:00:00+00:00"), path)

        loaded = load_last_sync(path)

        assert loaded is not None
        assert loaded.finished_at == "2026-01-02T00:00:00+00:00"


class TestFromStatistics:
    def test_builds_per_kind_counts_and_stamps_timestamp(self) -> None:
        stats = SyncStatistics(
            per_media_type=[
                MediaTypeStats(
                    media_type="anime", total=6, updated=3, skipped=1, dry_run=0, errors=0, unmatched=2
                ),
            ]
        )

        entry = SyncHistoryEntry.from_statistics(stats)

        assert entry.finished_at  # non-empty timestamp was stamped
        assert entry.per_kind == {
            "anime": {"updated": 3, "skipped": 1, "dry_run": 0, "errors": 0, "unmatched": 2}
        }

    def test_multiple_media_types(self) -> None:
        stats = SyncStatistics(
            per_media_type=[
                MediaTypeStats(media_type="anime", updated=1),
                MediaTypeStats(media_type="manga", updated=2),
            ]
        )

        entry = SyncHistoryEntry.from_statistics(stats)

        assert set(entry.per_kind) == {"anime", "manga"}
        assert entry.per_kind["manga"]["updated"] == 2
