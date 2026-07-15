"""Tests for mappings.yaml load/save: manual ID mappings and ignore rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from al_mal_sync.mapping.manual_mappings import (
    MappingsConfig,
    MappingsError,
    load_mappings,
)


class TestManualMappingLookup:
    def test_get_manual_mal_id_found(self) -> None:
        config = MappingsConfig()
        config.add_manual_mapping(anilist_id=1, mal_id=100)
        assert config.get_manual_mal_id(1) == 100

    def test_get_manual_anilist_id_found(self) -> None:
        config = MappingsConfig()
        config.add_manual_mapping(anilist_id=1, mal_id=100)
        assert config.get_manual_anilist_id(100) == 1

    def test_add_manual_mapping_updates_existing_entry(self) -> None:
        config = MappingsConfig()
        config.add_manual_mapping(anilist_id=1, mal_id=100)
        config.add_manual_mapping(anilist_id=1, mal_id=200, comment="corrected")
        assert config.get_manual_mal_id(1) == 200
        assert len(config.manual_mappings) == 1


class TestIgnoreRules:
    def test_is_ignored_by_id(self) -> None:
        config = MappingsConfig()
        config.add_ignore_by_id(42)
        assert config.is_ignored(42, "Anything") is True

    def test_is_ignored_by_title_case_insensitive(self) -> None:
        config = MappingsConfig()
        config.ignore.titles.append("Some Title")
        assert config.is_ignored(0, "some title") is True

    def test_add_ignore_by_id_is_idempotent(self) -> None:
        config = MappingsConfig()
        config.add_ignore_by_id(42)
        config.add_ignore_by_id(42)
        assert config.ignore.anilist_ids == [42]

    def test_is_ignored_by_mal_id(self) -> None:
        config = MappingsConfig()
        config.add_ignore_by_mal_id(55)
        assert config.is_ignored_by_mal_id(55) is True
        assert config.is_ignored_by_mal_id(56) is False


class TestLoadMappings:
    def test_missing_file_returns_empty_config(self, tmp_path: Path) -> None:
        config = load_mappings(tmp_path / "nonexistent.yaml")
        assert config.manual_mappings == []
        assert config.ignore.anilist_ids == []

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "mappings.yaml"
        config = MappingsConfig()
        config.add_manual_mapping(anilist_id=1, mal_id=100, comment="Season 2")
        config.add_ignore_by_id(99)
        config.ignore.titles.append("Skip This")
        config.save(path)

        loaded = load_mappings(path)

        assert loaded.get_manual_mal_id(1) == 100
        assert loaded.manual_mappings[0].comment == "Season 2"
        assert loaded.is_ignored(99, "")
        assert loaded.is_ignored(0, "skip this")

    def test_save_omits_empty_sections(self, tmp_path: Path) -> None:
        path = tmp_path / "mappings.yaml"
        MappingsConfig().save(path)
        assert path.read_text(encoding="utf-8").strip() == "{}"

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "mappings.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(MappingsError):
            load_mappings(path)
