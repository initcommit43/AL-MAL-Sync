"""Tests for xml_list.py: MAL-format XML parsing/writing, and that parsed
entries feed into Anime.from_mal_entry/Manga.from_mal_entry exactly like a
live MAL API response would."""

from __future__ import annotations

from datetime import date

import pytest

from al_mal_sync import xml_list
from al_mal_sync.models import Anime, AnimeStatus, Manga, MangaStatus

_ANIME_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<myanimelist>
<myinfo>
<user_export_type>1</user_export_type>
</myinfo>
<anime>
<series_animedb_id>1</series_animedb_id>
<series_title><![CDATA[Cowboy Bebop]]></series_title>
<series_episodes>26</series_episodes>
<my_id>0</my_id>
<my_watched_episodes>26</my_watched_episodes>
<my_start_date>2020-01-01</my_start_date>
<my_finish_date>2020-02-01</my_finish_date>
<my_score>9</my_score>
<my_status>Completed</my_status>
<my_rewatching>0</my_rewatching>
<update_on_import>1</update_on_import>
</anime>
<anime>
<series_animedb_id>0</series_animedb_id>
<series_title><![CDATA[No MAL ID]]></series_title>
<series_episodes>12</series_episodes>
<my_watched_episodes>1</my_watched_episodes>
<my_start_date>0000-00-00</my_start_date>
<my_finish_date>0000-00-00</my_finish_date>
<my_score>0</my_score>
<my_status>Watching</my_status>
</anime>
</myanimelist>
"""

_MANGA_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<myanimelist>
<myinfo>
<user_export_type>2</user_export_type>
</myinfo>
<manga>
<manga_mangadb_id>2</manga_mangadb_id>
<manga_title><![CDATA[Berserk]]></manga_title>
<manga_volumes>0</manga_volumes>
<manga_chapters>0</manga_chapters>
<my_id>0</my_id>
<my_read_volumes>3</my_read_volumes>
<my_read_chapters>50</my_read_chapters>
<my_start_date>2019-05-01</my_start_date>
<my_finish_date>0000-00-00</my_finish_date>
<my_score>10</my_score>
<my_status>Reading</my_status>
<my_rereadingg>1</my_rereadingg>
</manga>
</myanimelist>
"""


class TestDetectKind:
    def test_detects_anime(self) -> None:
        assert xml_list.detect_kind(_ANIME_XML) == "anime"

    def test_detects_manga(self) -> None:
        assert xml_list.detect_kind(_MANGA_XML) == "manga"

    def test_raises_on_malformed_xml(self) -> None:
        with pytest.raises(xml_list.XmlListError):
            xml_list.detect_kind("<not-xml")

    def test_raises_when_no_entries_found(self) -> None:
        with pytest.raises(xml_list.XmlListError):
            xml_list.detect_kind("<myanimelist><myinfo/></myanimelist>")


class TestParseAnimeXml:
    def test_skips_entries_with_no_mal_id(self) -> None:
        entries = xml_list.parse_anime_xml(_ANIME_XML)
        assert len(entries) == 1
        assert entries[0].anime.id == 1

    def test_parses_status_score_and_progress(self) -> None:
        entry = xml_list.parse_anime_xml(_ANIME_XML)[0]
        assert entry.anime.title == "Cowboy Bebop"
        assert entry.anime.num_episodes == 26
        assert entry.status.status == "completed"
        assert entry.status.score == 9
        assert entry.status.num_episodes_watched == 26
        assert entry.status.start_date == "2020-01-01"
        assert entry.status.finish_date == "2020-02-01"

    def test_feeds_into_anime_from_mal_entry(self) -> None:
        entry = xml_list.parse_anime_xml(_ANIME_XML)[0]
        anime = Anime.from_mal_entry(entry, reverse=False)
        assert anime.id_mal == 1
        assert anime.status == AnimeStatus.COMPLETED
        assert anime.started_at == date(2020, 1, 1)
        assert anime.finished_at == date(2020, 2, 1)

    def test_placeholder_dates_parse_as_none(self) -> None:
        # The second XML entry has no MAL id and is skipped by parse_anime_xml
        # itself -- exercise the placeholder date handling directly instead.
        entries = xml_list.parse_anime_xml(
            _ANIME_XML.replace("<series_animedb_id>0</series_animedb_id>", "<series_animedb_id>5</series_animedb_id>", 1)
        )
        no_id_entry = next(e for e in entries if e.anime.id == 5)
        anime = Anime.from_mal_entry(no_id_entry, reverse=False)
        assert anime.started_at is None
        assert anime.finished_at is None


class TestParseMangaXml:
    def test_parses_rereadingg_typo_field(self) -> None:
        entry = xml_list.parse_manga_xml(_MANGA_XML)[0]
        assert entry.status.is_rereading is True

    def test_feeds_into_manga_from_mal_entry(self) -> None:
        entry = xml_list.parse_manga_xml(_MANGA_XML)[0]
        manga = Manga.from_mal_entry(entry, reverse=False)
        assert manga.id_mal == 2
        assert manga.status == MangaStatus.READING
        assert manga.progress == 50
        assert manga.progress_volumes == 3
        assert manga.is_rereading is True


class TestWriteAndRoundTrip:
    def test_anime_round_trips_through_xml(self) -> None:
        anime = Anime(
            id_mal=42,
            title_en="Steins;Gate",
            status=AnimeStatus.WATCHING,
            score=8,
            progress=12,
            num_episodes=24,
            started_at=date(2021, 3, 1),
            finished_at=None,
            is_rewatching=True,
        )
        xml_text = xml_list.anime_list_to_xml([anime], username="tester")
        assert xml_list.detect_kind(xml_text) == "anime"

        [parsed] = xml_list.parse_anime_xml(xml_text)
        round_tripped = Anime.from_mal_entry(parsed, reverse=False)
        assert round_tripped.id_mal == 42
        assert round_tripped.status == AnimeStatus.WATCHING
        assert round_tripped.score == 8
        assert round_tripped.progress == 12
        assert round_tripped.started_at == date(2021, 3, 1)
        assert round_tripped.finished_at is None
        assert round_tripped.is_rewatching is True

    def test_manga_round_trips_through_xml(self) -> None:
        manga = Manga(
            id_mal=7,
            title_en="Vinland Saga",
            status=MangaStatus.COMPLETED,
            score=10,
            progress=200,
            progress_volumes=25,
            started_at=date(2018, 1, 1),
            finished_at=date(2022, 1, 1),
            is_rereading=False,
        )
        xml_text = xml_list.manga_list_to_xml([manga], username="tester")
        assert xml_list.detect_kind(xml_text) == "manga"

        [parsed] = xml_list.parse_manga_xml(xml_text)
        round_tripped = Manga.from_mal_entry(parsed, reverse=False)
        assert round_tripped.id_mal == 7
        assert round_tripped.status == MangaStatus.COMPLETED
        assert round_tripped.score == 10
        assert round_tripped.progress == 200
        assert round_tripped.progress_volumes == 25
        assert round_tripped.started_at == date(2018, 1, 1)
        assert round_tripped.finished_at == date(2022, 1, 1)
