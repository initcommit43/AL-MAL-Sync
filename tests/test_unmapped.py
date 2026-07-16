"""Tests for unmapped state: per-run replace semantics, save/load round-trip
(atomic write), removal helpers for the future --fix flow, and the
transient-pipeline-entry -> persisted-record converter."""

from __future__ import annotations

from pathlib import Path

from al_mal_sync.models import Anime
from al_mal_sync.sync.updater import UnmatchedEntry as PipelineUnmatchedEntry
from al_mal_sync.unmapped import (
    UnmappedRecord,
    UnmappedState,
    load_unmapped_state,
    save_unmapped_state,
)


def _record(**overrides) -> UnmappedRecord:
    defaults = {
        "title": "Show",
        "anilist_id": 1,
        "mal_id": 0,
        "media_type": "anime",
        "direction": "forward",
        "reason": "no strategy matched",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return UnmappedRecord(**defaults)


class TestUnmappedStateReplaceRun:
    def test_replace_run_drops_only_matching_media_type_and_direction(self) -> None:
        state = UnmappedState()
        state.entries = [
            _record(title="Old Anime Forward", media_type="anime", direction="forward"),
            _record(title="Manga Forward", media_type="manga", direction="forward"),
            _record(title="Anime Reverse", media_type="anime", direction="reverse"),
        ]

        state.replace_run("anime", "forward", [_record(title="New Anime Forward")])

        titles = {e.title for e in state.entries}
        assert titles == {"New Anime Forward", "Manga Forward", "Anime Reverse"}

    def test_replace_run_with_empty_list_clears_that_slice(self) -> None:
        state = UnmappedState(entries=[_record(media_type="anime", direction="forward")])
        state.replace_run("anime", "forward", [])
        assert state.entries == []


class TestUnmappedStateRemoval:
    def test_remove_by_anilist_id(self) -> None:
        state = UnmappedState(entries=[_record(anilist_id=1), _record(anilist_id=2)])
        state.remove_by_anilist_id(1)
        assert [e.anilist_id for e in state.entries] == [2]

    def test_remove_by_mal_id(self) -> None:
        state = UnmappedState(entries=[_record(mal_id=10), _record(mal_id=20)])
        state.remove_by_mal_id(10)
        assert [e.mal_id for e in state.entries] == [20]

    def test_remove_by_title_case_insensitive(self) -> None:
        state = UnmappedState(entries=[_record(title="Some Show")])
        state.remove_by_title("some show")
        assert state.entries == []

    def test_clear(self) -> None:
        state = UnmappedState(entries=[_record(), _record()])
        state.clear()
        assert state.entries == []


class TestSaveLoadRoundtrip:
    def test_missing_file_returns_empty_state(self, tmp_path: Path) -> None:
        state = load_unmapped_state(tmp_path / "nonexistent.json")
        assert state.entries == []

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "unmapped.json"
        state = UnmappedState(entries=[_record(title="A"), _record(title="B", anilist_id=2)])

        save_unmapped_state(state, path)
        loaded = load_unmapped_state(path)

        assert [e.title for e in loaded.entries] == ["A", "B"]
        assert loaded.entries[1].anilist_id == 2

    def test_malformed_json_falls_back_to_empty_state(self, tmp_path: Path) -> None:
        path = tmp_path / "unmapped.json"
        path.write_text("not json", encoding="utf-8")
        state = load_unmapped_state(path)
        assert state.entries == []

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "state" / "unmapped.json"
        save_unmapped_state(UnmappedState(), path)
        assert path.exists()


class TestFromPipelineEntry:
    def test_extracts_ids_title_and_reason(self) -> None:
        source = Anime(id_anilist=5, id_mal=0, title_en="Some Show")
        pipeline_entry = PipelineUnmatchedEntry(source=source, reason="no strategy matched")

        record = UnmappedRecord.from_pipeline_entry(pipeline_entry, media_type="anime", direction="forward")

        assert record.title == "Some Show"
        assert record.anilist_id == 5
        assert record.mal_id == 0
        assert record.media_type == "anime"
        assert record.direction == "forward"
        assert record.reason == "no strategy matched"
        assert record.updated_at  # non-empty timestamp was stamped
