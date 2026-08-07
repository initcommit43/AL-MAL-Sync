"""Import/export of the standard MyAnimeList XML list format -- the same
schema myanimelist.net's own "Export list" feature produces, and the one
AniList's list importer accepts. AniList has no native list-export feature of
its own, so this format is what lets an AniList list round-trip through a
file; MAL gets the same import/export pair here for parity/scripting.

Deliberately parses/writes into the existing MAL API v2 shapes (`MALAnime`,
`MALAnimeListStatus`, `MALUserAnimeEntry`, ...) rather than inventing a
separate XML-specific model, so `Anime.from_mal_entry`/`Manga.from_mal_entry`
-- and therefore every existing score/date/status normalization rule -- apply
unchanged to XML-sourced data. Exporting works the other way: any `Anime`/
`Manga` (regardless of which service it came from) already carries MAL-scale
score/status, so it serializes to this format directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from .clients.myanimelist import (
    MALAnime,
    MALAnimeListStatus,
    MALManga,
    MALMangaListStatus,
    MALTitles,
    MALUserAnimeEntry,
    MALUserMangaEntry,
)

if TYPE_CHECKING:
    from .models import Anime, Manga

# MAL's XML uses human-readable status text, not the API v2 lowercase/
# underscore values -- these two dicts are the only place that difference
# needs to be bridged.
_XML_TO_API_ANIME_STATUS = {
    "Watching": "watching",
    "Completed": "completed",
    "On-Hold": "on_hold",
    "Dropped": "dropped",
    "Plan to Watch": "plan_to_watch",
}
_API_TO_XML_ANIME_STATUS = {v: k for k, v in _XML_TO_API_ANIME_STATUS.items()}

_XML_TO_API_MANGA_STATUS = {
    "Reading": "reading",
    "Completed": "completed",
    "On-Hold": "on_hold",
    "Dropped": "dropped",
    "Plan to Read": "plan_to_read",
}
_API_TO_XML_MANGA_STATUS = {v: k for k, v in _XML_TO_API_MANGA_STATUS.items()}


class XmlListError(Exception):
    """Raised for malformed/unrecognized MAL list XML."""


def _text(el: ET.Element, tag: str, default: str = "") -> str:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else default


def _int(el: ET.Element, tag: str, default: int = 0) -> int:
    value = _text(el, tag)
    try:
        return int(value)
    except ValueError:
        return default


def _bool(el: ET.Element, tag: str) -> bool:
    return _text(el, tag) in ("1", "true", "YES")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def detect_kind(xml_text: str) -> str:
    """"anime" or "manga", based on which entry tag the file actually
    contains (more reliable than trusting <user_export_type>, which some
    hand-edited or third-party-generated files get wrong or omit)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise XmlListError(f"invalid XML: {exc}") from exc

    if root.find("anime") is not None:
        return "anime"
    if root.find("manga") is not None:
        return "manga"
    raise XmlListError("no <anime> or <manga> entries found")


def parse_anime_xml(xml_text: str) -> list[MALUserAnimeEntry]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise XmlListError(f"invalid XML: {exc}") from exc

    entries = []
    for node in root.findall("anime"):
        mal_id = _int(node, "series_animedb_id")
        if mal_id <= 0:
            continue  # no MAL id -> nothing for the matching strategies to key off
        title = _text(node, "series_title")
        anime = MALAnime(
            id=mal_id,
            title=title,
            alternative_titles=MALTitles(en=title),
            num_episodes=_int(node, "series_episodes"),
        )
        status = MALAnimeListStatus(
            status=_XML_TO_API_ANIME_STATUS.get(_text(node, "my_status"), ""),
            score=_int(node, "my_score"),
            num_episodes_watched=_int(node, "my_watched_episodes"),
            is_rewatching=_bool(node, "my_rewatching"),
            start_date=_text(node, "my_start_date"),
            finish_date=_text(node, "my_finish_date"),
        )
        entries.append(MALUserAnimeEntry(anime=anime, status=status))
    return entries


def parse_manga_xml(xml_text: str) -> list[MALUserMangaEntry]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise XmlListError(f"invalid XML: {exc}") from exc

    entries = []
    for node in root.findall("manga"):
        mal_id = _int(node, "manga_mangadb_id")
        if mal_id <= 0:
            continue
        title = _text(node, "manga_title")
        manga = MALManga(
            id=mal_id,
            title=title,
            alternative_titles=MALTitles(en=title),
            num_volumes=_int(node, "manga_volumes"),
            num_chapters=_int(node, "manga_chapters"),
        )
        status = MALMangaListStatus(
            status=_XML_TO_API_MANGA_STATUS.get(_text(node, "my_status"), ""),
            score=_int(node, "my_score"),
            num_volumes_read=_int(node, "my_read_volumes"),
            num_chapters_read=_int(node, "my_read_chapters"),
            # MAL's own manga export has shipped the field as "my_rereadingg"
            # (extra "g") for years; accept both spellings.
            is_rereading=_bool(node, "my_rereadingg") or _bool(node, "my_rereading"),
            start_date=_text(node, "my_start_date"),
            finish_date=_text(node, "my_finish_date"),
        )
        entries.append(MALUserMangaEntry(manga=manga, status=status))
    return entries


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def _date_or_placeholder(value: object) -> str:
    text = str(value) if value else ""
    return text if text else "0000-00-00"


def _sub(parent: ET.Element, tag: str, value: object) -> None:
    child = ET.SubElement(parent, tag)
    child.text = str(value)


def anime_list_to_xml(entries: list[Anime], *, username: str = "") -> str:
    root = ET.Element("myanimelist")
    info = ET.SubElement(root, "myinfo")
    _sub(info, "user_name", username)
    _sub(info, "user_export_type", 1)
    _sub(info, "user_total_anime", len(entries))

    for anime in entries:
        node = ET.SubElement(root, "anime")
        _sub(node, "series_animedb_id", anime.id_mal)
        _sub(node, "series_title", anime.get_title())
        _sub(node, "series_episodes", anime.num_episodes)
        _sub(node, "my_id", 0)
        _sub(node, "my_watched_episodes", anime.progress)
        _sub(node, "my_start_date", _date_or_placeholder(anime.started_at))
        _sub(node, "my_finish_date", _date_or_placeholder(anime.finished_at))
        _sub(node, "my_score", anime.score)
        _sub(node, "my_status", _API_TO_XML_ANIME_STATUS.get(anime.status.value, "Plan to Watch"))
        _sub(node, "my_rewatching", 1 if anime.is_rewatching else 0)
        _sub(node, "update_on_import", 1)

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def manga_list_to_xml(entries: list[Manga], *, username: str = "") -> str:
    root = ET.Element("myanimelist")
    info = ET.SubElement(root, "myinfo")
    _sub(info, "user_name", username)
    _sub(info, "user_export_type", 2)
    _sub(info, "user_total_manga", len(entries))

    for manga in entries:
        node = ET.SubElement(root, "manga")
        _sub(node, "manga_mangadb_id", manga.id_mal)
        _sub(node, "manga_title", manga.get_title())
        _sub(node, "manga_volumes", manga.volumes)
        _sub(node, "manga_chapters", manga.chapters)
        _sub(node, "my_id", 0)
        _sub(node, "my_read_volumes", manga.progress_volumes)
        _sub(node, "my_read_chapters", manga.progress)
        _sub(node, "my_start_date", _date_or_placeholder(manga.started_at))
        _sub(node, "my_finish_date", _date_or_placeholder(manga.finished_at))
        _sub(node, "my_score", manga.score)
        _sub(node, "my_status", _API_TO_XML_MANGA_STATUS.get(manga.status.value, "Plan to Read"))
        _sub(node, "my_rereadingg", 1 if manga.is_rereading else 0)
        _sub(node, "update_on_import", 1)

    return ET.tostring(root, encoding="unicode", xml_declaration=False)
