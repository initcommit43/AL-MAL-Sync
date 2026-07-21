"""MyAnimeList API v2 client.

Ported from the reference Go tool's myanimelist.go (using the same field sets and
endpoint paths as its go-myanimelist dependency). REST, not GraphQL: list/search
endpoints are GET with query params, updates are PATCH with a form-encoded body.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from ..http_retry import RetryableSession

if TYPE_CHECKING:
    from ..oauth import OAuth

BASE_URL = "https://api.myanimelist.net/v2/"

# Same field sets as the reference tool's animeFields/mangaFields: id, title, and
# main_picture come back by default, everything else must be requested explicitly.
ANIME_FIELDS = "alternative_titles,num_episodes,my_list_status,start_season"
MANGA_FIELDS = "alternative_titles,num_volumes,num_chapters,my_list_status,start_date"

LIST_PAGE_SIZE = 100
SEARCH_ANIME_LIMIT = 3
SEARCH_MANGA_LIMIT = 10


class MyAnimeListAPIError(Exception):
    """Raised for MyAnimeList API request failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MyAnimeListNotFoundError(MyAnimeListAPIError):
    """Raised when a requested anime/manga/list entry doesn't exist (HTTP 404)."""


# --------------------------------------------------------------------------
# Response shapes
# --------------------------------------------------------------------------


@dataclass
class MALTitles:
    en: str = ""
    ja: str = ""
    synonyms: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MALTitles:
        data = data or {}
        return cls(
            en=data.get("en", ""),
            ja=data.get("ja", ""),
            synonyms=list(data.get("synonyms") or []),
        )


@dataclass
class MALAnimeListStatus:
    status: str = ""
    score: int = 0
    num_episodes_watched: int = 0
    is_rewatching: bool = False
    start_date: str = ""
    finish_date: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MALAnimeListStatus:
        data = data or {}
        return cls(
            status=data.get("status", ""),
            score=data.get("score", 0),
            num_episodes_watched=data.get("num_episodes_watched", 0),
            is_rewatching=data.get("is_rewatching", False),
            start_date=data.get("start_date", ""),
            finish_date=data.get("finish_date", ""),
        )


@dataclass
class MALAnime:
    id: int
    title: str = ""
    alternative_titles: MALTitles = field(default_factory=MALTitles)
    num_episodes: int = 0
    start_season_year: int | None = None
    start_season: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MALAnime:
        season = data.get("start_season") or {}
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            alternative_titles=MALTitles.from_dict(data.get("alternative_titles")),
            num_episodes=data.get("num_episodes", 0),
            start_season_year=season.get("year"),
            start_season=season.get("season", ""),
        )


@dataclass
class MALUserAnimeEntry:
    anime: MALAnime
    status: MALAnimeListStatus

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MALUserAnimeEntry:
        node = data["node"]
        return cls(
            anime=MALAnime.from_dict(node),
            status=MALAnimeListStatus.from_dict(node.get("my_list_status")),
        )


@dataclass
class MALMangaListStatus:
    status: str = ""
    score: int = 0
    num_volumes_read: int = 0
    num_chapters_read: int = 0
    is_rereading: bool = False
    start_date: str = ""
    finish_date: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MALMangaListStatus:
        data = data or {}
        return cls(
            status=data.get("status", ""),
            score=data.get("score", 0),
            num_volumes_read=data.get("num_volumes_read", 0),
            num_chapters_read=data.get("num_chapters_read", 0),
            is_rereading=data.get("is_rereading", False),
            start_date=data.get("start_date", ""),
            finish_date=data.get("finish_date", ""),
        )


@dataclass
class MALManga:
    id: int
    title: str = ""
    alternative_titles: MALTitles = field(default_factory=MALTitles)
    num_volumes: int = 0
    num_chapters: int = 0
    start_date: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MALManga:
        return cls(
            id=data["id"],
            title=data.get("title", ""),
            alternative_titles=MALTitles.from_dict(data.get("alternative_titles")),
            num_volumes=data.get("num_volumes", 0),
            num_chapters=data.get("num_chapters", 0),
            start_date=data.get("start_date", ""),
        )


@dataclass
class MALUserMangaEntry:
    manga: MALManga
    status: MALMangaListStatus

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MALUserMangaEntry:
        node = data["node"]
        return cls(
            manga=MALManga.from_dict(node),
            status=MALMangaListStatus.from_dict(node.get("my_list_status")),
        )


def _parse_next_offset(paging: dict[str, Any]) -> int | None:
    """Extract the `offset` query param from a paging.next URL, MAL's only way
    of telling you there's another page (there's no total-count field)."""
    next_url = paging.get("next")
    if not next_url:
        return None
    query = urllib.parse.urlparse(next_url).query
    values = urllib.parse.parse_qs(query).get("offset")
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _extract_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        return payload.get("message") or payload.get("error") or response.text[:200]
    return response.text[:200]


def _format_mal_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class MyAnimeListClient:
    def __init__(self, oauth: OAuth, username: str, *, http_timeout: float = 30.0) -> None:
        self.oauth = oauth
        self.username = username
        self.http_timeout = http_timeout
        self.session = RetryableSession(max_retries=3)

    def _headers(self) -> dict[str, str]:
        token = self.oauth.get_valid_token()
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(
            BASE_URL + path, headers=self._headers(), params=params, timeout=self.http_timeout
        )
        return self._parse_response(response)

    def _patch(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        response = self.session.patch(
            BASE_URL + path, headers=self._headers(), data=data, timeout=self.http_timeout
        )
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> dict[str, Any]:
        if response.status_code == 404:
            raise MyAnimeListNotFoundError(f"not found: {response.url}", status_code=404)
        if not response.ok:
            raise MyAnimeListAPIError(
                f"MyAnimeList API error {response.status_code}: {_extract_error_message(response)}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise MyAnimeListAPIError(f"invalid JSON response: {exc}") from exc

    def get_authenticated_username(self) -> str:
        """The MyAnimeList username the current OAuth token belongs to --
        used to auto-fill Settings' username field right after login, so a
        user never has to look up and type their own username by hand."""
        data = self._get("users/@me", params={"fields": "name"})
        return str(data["name"])

    def get_user_anime_list(self) -> list[MALUserAnimeEntry]:
        entries: list[MALUserAnimeEntry] = []
        offset = 0
        while True:
            data = self._get(
                f"users/{self.username}/animelist",
                params={"fields": ANIME_FIELDS, "limit": LIST_PAGE_SIZE, "offset": offset},
            )
            entries.extend(MALUserAnimeEntry.from_dict(item) for item in data.get("data", []))
            next_offset = _parse_next_offset(data.get("paging") or {})
            if next_offset is None:
                break
            offset = next_offset
        return entries

    def get_user_manga_list(self) -> list[MALUserMangaEntry]:
        entries: list[MALUserMangaEntry] = []
        offset = 0
        while True:
            data = self._get(
                f"users/{self.username}/mangalist",
                params={"fields": MANGA_FIELDS, "limit": LIST_PAGE_SIZE, "offset": offset},
            )
            entries.extend(MALUserMangaEntry.from_dict(item) for item in data.get("data", []))
            next_offset = _parse_next_offset(data.get("paging") or {})
            if next_offset is None:
                break
            offset = next_offset
        return entries

    def get_anime_by_id(self, anime_id: int) -> MALAnime:
        if anime_id <= 0:
            raise ValueError("anime_id must be positive")
        data = self._get(f"anime/{anime_id}", params={"fields": ANIME_FIELDS})
        return MALAnime.from_dict(data)

    def get_animes_by_name(self, name: str) -> list[MALAnime]:
        data = self._get(
            "anime", params={"q": name, "fields": ANIME_FIELDS, "limit": SEARCH_ANIME_LIMIT}
        )
        return [MALAnime.from_dict(item["node"]) for item in data.get("data", [])]

    def get_manga_by_id(self, manga_id: int) -> MALManga:
        if manga_id <= 0:
            raise ValueError("manga_id must be positive")
        data = self._get(f"manga/{manga_id}", params={"fields": MANGA_FIELDS})
        return MALManga.from_dict(data)

    def get_mangas_by_name(self, name: str) -> list[MALManga]:
        data = self._get(
            "manga", params={"q": name, "fields": MANGA_FIELDS, "limit": SEARCH_MANGA_LIMIT}
        )
        return [MALManga.from_dict(item["node"]) for item in data.get("data", [])]

    def update_anime(
        self,
        anime_id: int,
        *,
        status: str | None = None,
        score: int | None = None,
        num_watched_episodes: int | None = None,
        start_date: date | None = None,
        finish_date: date | None = None,
        is_rewatching: bool | None = None,
    ) -> MALAnimeListStatus:
        fields: dict[str, str] = {}
        if status is not None:
            fields["status"] = status
        if score is not None:
            fields["score"] = str(score)
        if num_watched_episodes is not None:
            fields["num_watched_episodes"] = str(num_watched_episodes)
        if start_date is not None:
            fields["start_date"] = _format_mal_date(start_date)
        if finish_date is not None:
            fields["finish_date"] = _format_mal_date(finish_date)
        if is_rewatching is not None:
            fields["is_rewatching"] = "true" if is_rewatching else "false"
        if not fields:
            raise ValueError("update_anime called with no fields to update")

        data = self._patch(f"anime/{anime_id}/my_list_status", fields)
        return MALAnimeListStatus.from_dict(data)

    def update_manga(
        self,
        manga_id: int,
        *,
        status: str | None = None,
        score: int | None = None,
        num_chapters_read: int | None = None,
        num_volumes_read: int | None = None,
        start_date: date | None = None,
        finish_date: date | None = None,
        is_rereading: bool | None = None,
    ) -> MALMangaListStatus:
        fields: dict[str, str] = {}
        if status is not None:
            fields["status"] = status
        if score is not None:
            fields["score"] = str(score)
        if num_chapters_read is not None:
            fields["num_chapters_read"] = str(num_chapters_read)
        if num_volumes_read is not None:
            fields["num_volumes_read"] = str(num_volumes_read)
        if start_date is not None:
            fields["start_date"] = _format_mal_date(start_date)
        if finish_date is not None:
            fields["finish_date"] = _format_mal_date(finish_date)
        if is_rereading is not None:
            fields["is_rereading"] = "true" if is_rereading else "false"
        if not fields:
            raise ValueError("update_manga called with no fields to update")

        data = self._patch(f"manga/{manga_id}/my_list_status", fields)
        return MALMangaListStatus.from_dict(data)
