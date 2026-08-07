"""Tests for sync/xml_sync.py: run_export()/run_import() wiring against
faked AniList/MyAnimeList clients (no real network/OAuth/cache access),
mirroring test_sync_runner.py's approach for the live-sync equivalent."""

from __future__ import annotations

from typing import Any

import pytest

from al_mal_sync import xml_list
from al_mal_sync.clients.anilist import AniListDate, AniListListEntry, AniListMedia, AniListTitle
from al_mal_sync.clients.myanimelist import (
    MALAnime,
    MALAnimeListStatus,
    MALTitles,
    MALUserAnimeEntry,
)
from al_mal_sync.config import Config
from al_mal_sync.mapping.manual_mappings import MappingsConfig
from al_mal_sync.sync import xml_sync
from al_mal_sync.unmapped import UnmappedState


class _FakeAniListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_score_format(self) -> str:
        return "POINT_10"

    def get_user_anime_list(self) -> list[AniListListEntry]:
        return [
            AniListListEntry(
                id=1,
                media=AniListMedia(id=100, id_mal=1, title=AniListTitle(english="Cowboy Bebop"), episodes=26),
                status="COMPLETED",
                score=9.0,
                progress=26,
                started_at=AniListDate(year=2020, month=1, day=1),
                completed_at=AniListDate(year=2020, month=2, day=1),
            )
        ]

    def get_user_manga_list(self) -> list[Any]:
        return []


class _FakeMyAnimeListClient:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def get_user_anime_list(self) -> list[MALUserAnimeEntry]:
        return [
            MALUserAnimeEntry(
                anime=MALAnime(id=1, title="Cowboy Bebop", alternative_titles=MALTitles(en="Cowboy Bebop"), num_episodes=26),
                status=MALAnimeListStatus(
                    status="completed", score=9, num_episodes_watched=26,
                    start_date="2020-01-01", finish_date="2020-02-01",
                ),
            )
        ]

    def get_user_manga_list(self) -> list[Any]:
        return []


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xml_sync, "create_anilist_oauth", lambda config: object())
    monkeypatch.setattr(xml_sync, "create_myanimelist_oauth", lambda config: object())
    monkeypatch.setattr(xml_sync, "AniListClient", _FakeAniListClient)
    monkeypatch.setattr(xml_sync, "MyAnimeListClient", _FakeMyAnimeListClient)
    monkeypatch.setattr(xml_sync, "load_mappings", lambda path=None: MappingsConfig())
    monkeypatch.setattr(xml_sync, "load_unmapped_state", lambda path=None: UnmappedState())
    monkeypatch.setattr(xml_sync, "save_unmapped_state", lambda state, path=None: None)


def _config() -> Config:
    config = Config()
    config.offline_database.enabled = False
    config.hato_api.enabled = False
    config.arm_api.enabled = False
    config.jikan_api.enabled = False
    return config


class TestRunExport:
    def test_exports_anilist_anime_list_to_xml(self) -> None:
        documents = xml_sync.run_export(_config(), service="anilist", manga=False, all_media=False)
        assert set(documents) == {"anime"}
        parsed = xml_list.parse_anime_xml(documents["anime"])
        assert len(parsed) == 1
        assert parsed[0].anime.id == 1
        assert parsed[0].anime.title == "Cowboy Bebop"
        assert parsed[0].status.status == "completed"

    def test_exports_myanimelist_anime_list_to_xml(self) -> None:
        documents = xml_sync.run_export(_config(), service="myanimelist", manga=False, all_media=False)
        parsed = xml_list.parse_anime_xml(documents["anime"])
        assert parsed[0].anime.id == 1
        assert parsed[0].status.num_episodes_watched == 26

    def test_all_media_exports_both_kinds(self) -> None:
        documents = xml_sync.run_export(_config(), service="anilist", manga=False, all_media=True)
        assert set(documents) == {"anime", "manga"}

    def test_rejects_unknown_service(self) -> None:
        with pytest.raises(xml_sync.XmlSyncError):
            xml_sync.run_export(_config(), service="bogus")


class TestRunImport:
    _XML = """<?xml version="1.0" encoding="UTF-8" ?>
<myanimelist>
<myinfo><user_export_type>1</user_export_type></myinfo>
<anime>
<series_animedb_id>1</series_animedb_id>
<series_title><![CDATA[Cowboy Bebop]]></series_title>
<series_episodes>26</series_episodes>
<my_watched_episodes>26</my_watched_episodes>
<my_start_date>2020-01-01</my_start_date>
<my_finish_date>2020-02-01</my_finish_date>
<my_score>9</my_score>
<my_status>Completed</my_status>
</anime>
</myanimelist>
"""

    def test_import_into_myanimelist_matches_existing_entry_by_id(self) -> None:
        kind, outcome = xml_sync.run_import(
            _config(), xml_text=self._XML, target_service="myanimelist",
        )
        assert kind == "anime"
        # Same status/score/progress as the fake MAL list already has -> no update needed.
        assert outcome.skipped
        assert not outcome.errors

    def test_import_into_anilist_with_force_resolves_kind_and_runs(self) -> None:
        kind, outcome = xml_sync.run_import(
            _config(), xml_text=self._XML, target_service="anilist", force=True,
        )
        assert kind == "anime"
        # Forced mode needs a known AniList id, which a bare XML entry never
        # has -> every source is reported unmatched rather than erroring.
        assert len(outcome.unmatched) == 1
        assert not outcome.errors

    def test_rejects_unknown_service(self) -> None:
        with pytest.raises(xml_sync.XmlSyncError):
            xml_sync.run_import(_config(), xml_text=self._XML, target_service="bogus")

    def test_rejects_malformed_xml(self) -> None:
        with pytest.raises(xml_list.XmlListError):
            xml_sync.run_import(_config(), xml_text="<not-xml", target_service="myanimelist")
