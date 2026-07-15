"""Jikan (unofficial MAL) API client, used for manga ID mapping and MAL favorites reads.

Ported from the reference Go tool's jikan_api.go + jikan_cache.go. Includes the
title-matching helpers (search_titles_for_jikan / find_best_jikan_match /
match_jikan_manga_to_source) that strategies.py's JikanApiStrategy calls, since
they're specific to interpreting Jikan's response shape.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..http_retry import RetryableSession
from ..models import normalize_title, title_matching_levels

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.jikan.moe/v4"
DEFAULT_CACHE_MAX_AGE_SECONDS = 168 * 3600  # 7 days
MIN_REQUEST_INTERVAL_SECONDS = 0.5  # ~2 req/s; Jikan's actual limit is 3 req/s
CACHE_FILE_NAME = "mappings.json"


class JikanApiError(Exception):
    """Raised when a Jikan request fails outright (not a 404)."""


@dataclass
class JikanMangaData:
    mal_id: int = 0
    title: str = ""
    title_english: str = ""
    title_japanese: str = ""
    title_synonyms: list[str] = field(default_factory=list)
    type: str = ""
    chapters: int = 0
    volumes: int = 0
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mal_id": self.mal_id,
            "title": self.title,
            "title_english": self.title_english,
            "title_japanese": self.title_japanese,
            "title_synonyms": self.title_synonyms,
            "type": self.type,
            "chapters": self.chapters,
            "volumes": self.volumes,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JikanMangaData:
        return cls(
            mal_id=data.get("mal_id", 0),
            title=data.get("title") or "",
            title_english=data.get("title_english") or "",
            title_japanese=data.get("title_japanese") or "",
            title_synonyms=list(data.get("title_synonyms") or []),
            type=data.get("type") or "",
            chapters=data.get("chapters") or 0,
            volumes=data.get("volumes") or 0,
            status=data.get("status") or "",
        )


class JikanCache:
    """Persistent JSON cache for Jikan responses, keyed by "manga_{id}" or
    "search_{normalized_query}", with per-entry max-age expiry."""

    def __init__(
        self, cache_dir: str, *, max_age_seconds: float = DEFAULT_CACHE_MAX_AGE_SECONDS
    ) -> None:
        self.path = Path(cache_dir) / CACHE_FILE_NAME
        self.max_age_seconds = max_age_seconds
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            self._entries = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("failed to load Jikan cache: %s (starting fresh)", exc)
            self._entries = {}

    def size(self) -> int:
        return len(self._entries)

    def _get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.max_age_seconds > 0 and time.time() - entry.get("cached_at", 0) > self.max_age_seconds:
            return None
        return entry["data"]

    def _set(self, key: str, data: Any) -> None:
        self._entries[key] = {"data": data, "cached_at": time.time()}
        self._dirty = True

    def get_manga(self, mal_id: int) -> tuple[JikanMangaData | None, bool]:
        """Returns (data, found). found=True with data=None means a cached
        negative result (previously confirmed the ID doesn't exist)."""
        data = self._get(f"manga_{mal_id}")
        if data is None:
            return None, False
        if data == "null":
            return None, True
        return JikanMangaData.from_dict(data), True

    def set_manga(self, mal_id: int, data: JikanMangaData | None) -> None:
        self._set(f"manga_{mal_id}", data.to_dict() if data is not None else "null")

    def get_search(self, query: str) -> list[JikanMangaData] | None:
        data = self._get(f"search_{normalize_title(query)}")
        if data is None:
            return None
        return [JikanMangaData.from_dict(item) for item in data]

    def set_search(self, query: str, results: list[JikanMangaData]) -> None:
        self._set(f"search_{normalize_title(query)}", [r.to_dict() for r in results])

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._entries, indent=2)

        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix="jikan-", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        self._dirty = False
        logger.debug("saved %d Jikan cache entries to %s", len(self._entries), self.path)


class JikanClient:
    def __init__(
        self,
        cache_dir: str,
        *,
        cache_max_age_seconds: float = DEFAULT_CACHE_MAX_AGE_SECONDS,
        http_timeout: float = 15.0,
    ) -> None:
        self.base_url = DEFAULT_BASE_URL
        self.http_timeout = http_timeout
        self.session = RetryableSession(max_retries=2)
        self.cache = JikanCache(cache_dir, max_age_seconds=cache_max_age_seconds)
        self._rate_lock = threading.Lock()
        self._last_request = 0.0
        logger.info("Jikan cache loaded (%d entries)", self.cache.size())

    def _rate_limit(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
            self._last_request = time.monotonic()

    def get_manga_by_mal_id(self, mal_id: int) -> JikanMangaData | None:
        """Errors are non-fatal here (logged, treated as not-found), matching
        the Go client: a broken Jikan lookup shouldn't abort the whole sync."""
        if mal_id <= 0:
            return None

        cached, found = self.cache.get_manga(mal_id)
        if found:
            logger.debug(
                "[JIKAN CACHE] hit: manga %d -> %s",
                mal_id, cached.title if cached else "not found (cached)",
            )
            return cached

        self._rate_limit()
        try:
            response = self.session.get(f"{self.base_url}/manga/{mal_id}", timeout=self.http_timeout)
        except Exception as exc:  # noqa: BLE001 (deliberately non-fatal, see docstring)
            logger.debug("[JIKAN API] manga %d: error: %s", mal_id, exc)
            return None

        if response.status_code == 404:
            logger.debug("[JIKAN API] manga %d: not found (404)", mal_id)
            self.cache.set_manga(mal_id, None)
            return None
        if not response.ok:
            logger.debug(
                "[JIKAN API] manga %d: unexpected status %d", mal_id, response.status_code
            )
            return None

        try:
            data = JikanMangaData.from_dict(response.json()["data"])
        except (ValueError, KeyError) as exc:
            logger.debug("[JIKAN API] manga %d: decode error: %s", mal_id, exc)
            return None

        self.cache.set_manga(mal_id, data)
        logger.debug("[JIKAN API] manga %d: found -> %s", mal_id, data.title)
        return data

    def search_manga(self, query: str) -> list[JikanMangaData]:
        """Errors are non-fatal here too; see get_manga_by_mal_id."""
        if not query:
            return []

        cached = self.cache.get_search(query)
        if cached is not None:
            logger.debug("[JIKAN CACHE] hit: search %r -> %d results", query, len(cached))
            return cached

        self._rate_limit()
        try:
            response = self.session.get(
                f"{self.base_url}/manga", params={"q": query}, timeout=self.http_timeout
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[JIKAN API] search %r: error: %s", query, exc)
            return []

        if not response.ok:
            logger.debug(
                "[JIKAN API] search %r: unexpected status %d", query, response.status_code
            )
            return []

        try:
            results = [JikanMangaData.from_dict(item) for item in response.json().get("data", [])]
        except ValueError as exc:
            logger.debug("[JIKAN API] search %r: decode error: %s", query, exc)
            return []

        self.cache.set_search(query, results)
        logger.debug("[JIKAN API] search %r: found %d results", query, len(results))
        return results

    def get_user_favorites(self, username: str) -> tuple[set[int], set[int]]:
        """Return (anime_mal_ids, manga_mal_ids) from a MAL user's public profile.

        Unlike the lookups above, failures here are raised rather than swallowed:
        favorites sync has no fallback if this fails, so the caller needs to know.
        """
        if not username:
            raise ValueError("username cannot be empty")

        self._rate_limit()
        url = f"{self.base_url}/users/{urllib.parse.quote(username)}/favorites"
        response = self.session.get(url, timeout=self.http_timeout)

        if response.status_code == 404:
            raise JikanApiError(f"user {username!r} not found or profile is private")
        if not response.ok:
            raise JikanApiError(
                f"failed to fetch favorites for {username!r}: status {response.status_code}"
            )

        data = response.json().get("data", {})
        anime_ids = {entry["mal_id"] for entry in data.get("anime", [])}
        manga_ids = {entry["mal_id"] for entry in data.get("manga", [])}
        return anime_ids, manga_ids

    def save_cache(self) -> None:
        self.cache.save()


def match_jikan_manga_to_source(
    jikan_data: JikanMangaData, src_title_en: str, src_title_jp: str, src_title_romaji: str
) -> bool:
    """Check whether a Jikan result matches a source manga by title, trying
    Jikan's canonical titles plus each of its synonyms."""
    candidates = [(jikan_data.title_english, jikan_data.title_japanese, jikan_data.title)]
    candidates.extend(
        (synonym, jikan_data.title_japanese, jikan_data.title)
        for synonym in jikan_data.title_synonyms
    )

    for en, jp, romaji in candidates:
        if title_matching_levels(src_title_en, src_title_jp, src_title_romaji, en, jp, romaji):
            return True

    # Cross-match: Jikan's fields don't line up cleanly with AniList's EN/JP/
    # romaji split, so also try source-English against Jikan's main "title"
    # (often romaji) and source-romaji against Jikan's English title.
    if (
        src_title_en
        and jikan_data.title
        and normalize_title(src_title_en) == normalize_title(jikan_data.title)
    ):
        return True
    return bool(
        src_title_romaji
        and jikan_data.title_english
        and normalize_title(src_title_romaji) == normalize_title(jikan_data.title_english)
    )


def find_best_jikan_match(
    results: list[JikanMangaData], src_title_en: str, src_title_jp: str, src_title_romaji: str
) -> int:
    """Return the MAL ID of the first matching result, or 0 if none match."""
    for result in results:
        if match_jikan_manga_to_source(result, src_title_en, src_title_jp, src_title_romaji):
            return result.mal_id
    return 0


def search_titles_for_jikan(title_en: str, title_romaji: str) -> list[str]:
    """Search queries to try against Jikan, in preference order and de-duplicated
    by normalized form. Romaji first, since Jikan's data skews Japanese-centric."""
    titles: list[str] = []
    seen: set[str] = set()
    for title in (title_romaji, title_en):
        if not title:
            continue
        normalized = normalize_title(title)
        if normalized in seen:
            continue
        seen.add(normalized)
        titles.append(title)
    return titles
