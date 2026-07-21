"""AniList GraphQL API client.

Ported from the reference Go tool's anilist.go (queries/mutations match its verniy
library calls field-for-field). Raw GraphQL over requests.post instead of a full
GraphQL client library, since we only need a handful of fixed operations (see
PLAN.md Phase 3 for the reasoning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from ..http_retry import RetryableSession

if TYPE_CHECKING:
    from ..oauth import OAuth

GRAPHQL_URL = "https://graphql.anilist.co"

# "status(version: 2)" matches the Go client's MediaFieldStatusV2: AniList's status
# field takes a version arg, and value 2 is what the site's own UI uses.
_STATUS_FIELD = "status(version: 2)"

USER_ANIME_LIST_QUERY = f"""
query ($username: String, $type: MediaType) {{
    MediaListCollection(userName: $username, type: $type) {{
        lists {{
            status
            entries {{
                id
                status
                score
                progress
                startedAt {{ year month day }}
                completedAt {{ year month day }}
                media {{
                    id
                    idMal
                    title {{ romaji english native }}
                    {_STATUS_FIELD}
                    episodes
                    seasonYear
                    isFavourite
                }}
            }}
        }}
    }}
}}
"""

USER_MANGA_LIST_QUERY = f"""
query ($username: String, $type: MediaType) {{
    MediaListCollection(userName: $username, type: $type) {{
        lists {{
            name
            status
            entries {{
                id
                status
                score
                progress
                progressVolumes
                startedAt {{ year month day }}
                completedAt {{ year month day }}
                media {{
                    id
                    idMal
                    title {{ romaji english native }}
                    type
                    format
                    {_STATUS_FIELD}
                    chapters
                    volumes
                    isFavourite
                }}
            }}
        }}
    }}
}}
"""

UPDATE_ANIME_MUTATION = """
mutation (
    $mediaId: Int, $status: MediaListStatus, $progress: Int, $score: Float,
    $startedAt: FuzzyDateInput, $completedAt: FuzzyDateInput
) {
    SaveMediaListEntry(
        mediaId: $mediaId, status: $status, progress: $progress, score: $score,
        startedAt: $startedAt, completedAt: $completedAt
    ) {
        id
        status
        progress
        score
    }
}
"""

UPDATE_MANGA_MUTATION = """
mutation (
    $mediaId: Int, $status: MediaListStatus, $progress: Int, $progressVolumes: Int,
    $score: Float, $startedAt: FuzzyDateInput, $completedAt: FuzzyDateInput
) {
    SaveMediaListEntry(
        mediaId: $mediaId, status: $status, progress: $progress,
        progressVolumes: $progressVolumes, score: $score,
        startedAt: $startedAt, completedAt: $completedAt
    ) {
        id
        status
        progress
        progressVolumes
        score
    }
}
"""

TOGGLE_FAVOURITE_MUTATION = """
mutation ($animeId: Int, $mangaId: Int) {
    ToggleFavourite(animeId: $animeId, mangaId: $mangaId) {
        anime { nodes { id } }
        manga { nodes { id } }
    }
}
"""

GET_ANIME_QUERY = f"""
query ($id: Int, $type: MediaType) {{
    Media(id: $id, type: $type) {{
        id
        idMal
        title {{ romaji english native }}
        {_STATUS_FIELD}
        episodes
        seasonYear
    }}
}}
"""

GET_MANGA_QUERY = f"""
query ($id: Int, $type: MediaType) {{
    Media(id: $id, type: $type) {{
        id
        idMal
        title {{ romaji english native }}
        type
        format
        {_STATUS_FIELD}
        chapters
        volumes
    }}
}}
"""

# Default sort matches the Go client's search default (popularity/score desc), used
# both for name search and MAL-ID lookup so result ordering stays consistent.
_SEARCH_SORT = "[POPULARITY_DESC, SCORE_DESC]"

SEARCH_ANIME_BY_NAME_QUERY = f"""
query ($search: String, $page: Int, $perPage: Int) {{
    Page(page: $page, perPage: $perPage) {{
        media(search: $search, type: ANIME, sort: {_SEARCH_SORT}) {{
            id
            idMal
            title {{ romaji english native }}
            {_STATUS_FIELD}
            episodes
            seasonYear
        }}
    }}
}}
"""

SEARCH_ANIME_BY_MAL_ID_QUERY = f"""
query ($idMal: Int, $page: Int, $perPage: Int) {{
    Page(page: $page, perPage: $perPage) {{
        media(idMal: $idMal, type: ANIME, sort: {_SEARCH_SORT}) {{
            id
            idMal
            title {{ romaji english native }}
            {_STATUS_FIELD}
            episodes
            seasonYear
        }}
    }}
}}
"""

SEARCH_MANGA_BY_NAME_QUERY = f"""
query ($search: String, $page: Int, $perPage: Int) {{
    Page(page: $page, perPage: $perPage) {{
        media(search: $search, type: MANGA, sort: {_SEARCH_SORT}) {{
            id
            idMal
            title {{ romaji english native }}
            type
            format
            {_STATUS_FIELD}
            chapters
            volumes
        }}
    }}
}}
"""

SEARCH_MANGA_BY_MAL_ID_QUERY = f"""
query ($idMal: Int, $page: Int, $perPage: Int) {{
    Page(page: $page, perPage: $perPage) {{
        media(idMal: $idMal, type: MANGA, sort: {_SEARCH_SORT}) {{
            id
            idMal
            title {{ romaji english native }}
            type
            format
            {_STATUS_FIELD}
            chapters
            volumes
        }}
    }}
}}
"""

GET_SCORE_FORMAT_QUERY = """
query ($name: String) {
    User(name: $name) {
        mediaListOptions {
            scoreFormat
        }
    }
}
"""

VIEWER_QUERY = """
query {
    Viewer {
        name
    }
}
"""


class AniListAPIError(Exception):
    """Raised for AniList GraphQL request/response failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AniListNotFoundError(AniListAPIError):
    """Raised when a requested media or user entry doesn't exist."""


# --------------------------------------------------------------------------
# Response shapes
# --------------------------------------------------------------------------


@dataclass
class AniListTitle:
    romaji: str = ""
    english: str = ""
    native: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AniListTitle:
        data = data or {}
        return cls(
            romaji=data.get("romaji") or "",
            english=data.get("english") or "",
            native=data.get("native") or "",
        )


@dataclass
class AniListDate:
    year: int | None = None
    month: int | None = None
    day: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AniListDate | None:
        if not data:
            return None
        return cls(year=data.get("year"), month=data.get("month"), day=data.get("day"))

    def to_date(self) -> date | None:
        # AniList allows partial dates (year-only, year+month). Treat anything
        # short of a full year/month/day as "no usable date" rather than guessing.
        if self.year is None or self.month is None or self.day is None:
            return None
        try:
            return date(self.year, self.month, self.day)
        except ValueError:
            return None


@dataclass
class AniListMedia:
    id: int
    id_mal: int | None = None
    title: AniListTitle = field(default_factory=AniListTitle)
    status: str = ""
    is_favourite: bool = False
    episodes: int | None = None  # anime only
    season_year: int | None = None  # anime only
    media_type: str = ""  # manga only ("type" is a Python builtin, avoid shadowing)
    format: str = ""  # manga only
    chapters: int | None = None  # manga only
    volumes: int | None = None  # manga only

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AniListMedia:
        return cls(
            id=data["id"],
            id_mal=data.get("idMal"),
            title=AniListTitle.from_dict(data.get("title")),
            status=data.get("status") or "",
            is_favourite=data.get("isFavourite", False),
            episodes=data.get("episodes"),
            season_year=data.get("seasonYear"),
            media_type=data.get("type") or "",
            format=data.get("format") or "",
            chapters=data.get("chapters"),
            volumes=data.get("volumes"),
        )


@dataclass
class AniListListEntry:
    id: int
    media: AniListMedia
    status: str = ""
    score: float = 0.0
    progress: int = 0
    progress_volumes: int = 0
    started_at: AniListDate | None = None
    completed_at: AniListDate | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AniListListEntry:
        return cls(
            id=data["id"],
            status=data.get("status") or "",
            score=data.get("score") or 0.0,
            progress=data.get("progress") or 0,
            progress_volumes=data.get("progressVolumes") or 0,
            started_at=AniListDate.from_dict(data.get("startedAt")),
            completed_at=AniListDate.from_dict(data.get("completedAt")),
            media=AniListMedia.from_dict(data["media"]),
        )


def _date_to_fuzzy(value: date) -> dict[str, int]:
    return {"year": value.year, "month": value.month, "day": value.day}


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class AniListClient:
    def __init__(self, oauth: OAuth, username: str, *, http_timeout: float = 30.0) -> None:
        self.oauth = oauth
        self.username = username
        self.http_timeout = http_timeout
        self.session = RetryableSession(max_retries=3)

    def _headers(self) -> dict[str, str]:
        token = self.oauth.get_valid_token()
        return {"Authorization": f"{token.token_type} {token.access_token}"}

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            GRAPHQL_URL,
            headers=self._headers(),
            json={"query": query, "variables": variables},
            timeout=self.http_timeout,
        )
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AniListAPIError(
                f"invalid JSON response (status {response.status_code}): {exc}"
            ) from exc

        if not response.ok:
            raise AniListAPIError(
                f"AniList API error {response.status_code}: {_format_graphql_errors(payload)}",
                status_code=response.status_code,
            )

        # AniList can return HTTP 200 with a populated "errors" array alongside
        # partial/null data. Checking this even on a 200 is stricter than the Go
        # client (which only inspects errors on non-200), and avoids silently
        # treating a failed mutation as a success.
        if isinstance(payload, dict) and payload.get("errors"):
            raise AniListAPIError(f"AniList GraphQL error: {_format_graphql_errors(payload)}")

        return payload.get("data") or {}

    def get_authenticated_username(self) -> str:
        """The AniList username the current OAuth token belongs to -- used to
        auto-fill Settings' username field right after login, so a user
        never has to look up and type their own username by hand."""
        data = self._post(VIEWER_QUERY, {})
        return str(data["Viewer"]["name"])

    def get_user_anime_list(self) -> list[AniListListEntry]:
        data = self._post(USER_ANIME_LIST_QUERY, {"username": self.username, "type": "ANIME"})
        return _flatten_list_collection(data)

    def get_user_manga_list(self) -> list[AniListListEntry]:
        data = self._post(USER_MANGA_LIST_QUERY, {"username": self.username, "type": "MANGA"})
        return _flatten_list_collection(data)

    def update_anime_entry(
        self,
        media_id: int,
        status: str,
        progress: int,
        score: int,
        *,
        started_at: date | None = None,
        completed_at: date | None = None,
    ) -> None:
        variables: dict[str, Any] = {
            "mediaId": media_id,
            "status": status,
            "progress": progress,
            "score": float(score),
        }
        # Omitted entirely (not sent as null) so a nil source date never clears an
        # existing date on AniList. See docs/date-sync.md.
        if started_at is not None:
            variables["startedAt"] = _date_to_fuzzy(started_at)
        if completed_at is not None:
            variables["completedAt"] = _date_to_fuzzy(completed_at)
        self._post(UPDATE_ANIME_MUTATION, variables)

    def update_manga_entry(
        self,
        media_id: int,
        status: str,
        progress: int,
        progress_volumes: int,
        score: int,
        *,
        started_at: date | None = None,
        completed_at: date | None = None,
    ) -> None:
        variables: dict[str, Any] = {
            "mediaId": media_id,
            "status": status,
            "progress": progress,
            "progressVolumes": progress_volumes,
            "score": float(score),
        }
        if started_at is not None:
            variables["startedAt"] = _date_to_fuzzy(started_at)
        if completed_at is not None:
            variables["completedAt"] = _date_to_fuzzy(completed_at)
        self._post(UPDATE_MANGA_MUTATION, variables)

    def toggle_favourite(self, *, anime_id: int = 0, manga_id: int = 0) -> None:
        """Toggle favorite status. Exactly one of anime_id/manga_id must be positive.

        Idempotent on AniList's side: calling it on an already-favorited item
        removes it, calling it on a non-favorited item adds it.
        """
        if anime_id <= 0 and manga_id <= 0:
            raise ValueError("at least one of anime_id or manga_id must be positive")
        if anime_id > 0 and manga_id > 0:
            raise ValueError("only one of anime_id or manga_id can be specified per call")

        variables: dict[str, Any] = {}
        if anime_id > 0:
            variables["animeId"] = anime_id
        if manga_id > 0:
            variables["mangaId"] = manga_id
        self._post(TOGGLE_FAVOURITE_MUTATION, variables)

    def get_anime_by_id(self, media_id: int) -> AniListMedia:
        data = self._post(GET_ANIME_QUERY, {"id": media_id, "type": "ANIME"})
        media = data.get("Media")
        if media is None:
            raise AniListNotFoundError(f"no anime found with ID {media_id}")
        return AniListMedia.from_dict(media)

    def get_manga_by_id(self, media_id: int) -> AniListMedia:
        data = self._post(GET_MANGA_QUERY, {"id": media_id, "type": "MANGA"})
        media = data.get("Media")
        if media is None:
            raise AniListNotFoundError(f"no manga found with ID {media_id}")
        return AniListMedia.from_dict(media)

    def get_animes_by_name(self, name: str, *, per_page: int = 10) -> list[AniListMedia]:
        data = self._post(
            SEARCH_ANIME_BY_NAME_QUERY, {"search": name, "page": 1, "perPage": per_page}
        )
        return [AniListMedia.from_dict(m) for m in _page_media(data)]

    def get_mangas_by_name(self, name: str, *, per_page: int = 10) -> list[AniListMedia]:
        data = self._post(
            SEARCH_MANGA_BY_NAME_QUERY, {"search": name, "page": 1, "perPage": per_page}
        )
        return [AniListMedia.from_dict(m) for m in _page_media(data)]

    def get_anime_by_mal_id(self, mal_id: int) -> AniListMedia:
        data = self._post(SEARCH_ANIME_BY_MAL_ID_QUERY, {"idMal": mal_id, "page": 1, "perPage": 1})
        media_list = _page_media(data)
        if not media_list:
            raise AniListNotFoundError(f"no anime found with MAL ID {mal_id}")
        return AniListMedia.from_dict(media_list[0])

    def get_manga_by_mal_id(self, mal_id: int) -> AniListMedia:
        data = self._post(SEARCH_MANGA_BY_MAL_ID_QUERY, {"idMal": mal_id, "page": 1, "perPage": 1})
        media_list = _page_media(data)
        if not media_list:
            raise AniListNotFoundError(f"no manga found with MAL ID {mal_id}")
        return AniListMedia.from_dict(media_list[0])

    def get_user_score_format(self) -> str:
        data = self._post(GET_SCORE_FORMAT_QUERY, {"name": self.username})
        user = data.get("User")
        if user is None:
            raise AniListNotFoundError(f"user not found: {self.username}")
        score_format = (user.get("mediaListOptions") or {}).get("scoreFormat")
        if not score_format:
            raise AniListAPIError("user score format is missing from response")
        return score_format


def _flatten_list_collection(data: dict[str, Any]) -> list[AniListListEntry]:
    collection = data.get("MediaListCollection") or {}
    entries: list[AniListListEntry] = []
    for group in collection.get("lists") or []:
        for raw_entry in group.get("entries") or []:
            entries.append(AniListListEntry.from_dict(raw_entry))
    return entries


def _page_media(data: dict[str, Any]) -> list[dict[str, Any]]:
    return (data.get("Page") or {}).get("media") or []


def _format_graphql_errors(payload: Any) -> str:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            return "; ".join(str(e.get("message", e)) for e in errors)
    return str(payload)[:200]
