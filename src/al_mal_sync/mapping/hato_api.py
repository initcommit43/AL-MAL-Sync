"""Hato API client for online anime/manga ID mapping.

Ported from the reference Go tool's hato_api.go + hato_cache.go, with one fix:
the Go version reads HatoAPIConfig.CacheMaxAge from config but never actually
passes it to the cache, so entries are kept forever regardless of the config
value. This version enforces max age, matching how the Jikan cache already
behaves in the Go tool (and matching what HATO_API_CACHE_MAX_AGE documents).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..http_retry import RetryableSession

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://hato.malupdaterosx.moe"
CACHE_FILE_NAME = "mappings.json"


@dataclass
class HatoMappingData:
    anidb_id: int | None = None
    anilist_id: int | None = None
    kitsu_id: int | None = None
    mal_id: int | None = None
    notify_id: str | None = None
    type: int | None = None
    type_str: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anidb_id": self.anidb_id,
            "anilist_id": self.anilist_id,
            "kitsu_id": self.kitsu_id,
            "mal_id": self.mal_id,
            "notify_id": self.notify_id,
            "type": self.type,
            "type_str": self.type_str,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HatoMappingData:
        data = data or {}
        return cls(
            anidb_id=data.get("anidb_id"),
            anilist_id=data.get("anilist_id"),
            kitsu_id=data.get("kitsu_id"),
            mal_id=data.get("mal_id"),
            notify_id=data.get("notify_id"),
            type=data.get("type"),
            type_str=data.get("type_str"),
        )


def _cache_key(service: str, media_type: str, id_: int) -> str:
    return f"{service}_{media_type}_{id_}"


class HatoCache:
    """Persistent JSON cache for Hato API responses, keyed by
    "{service}_{media_type}_{id}" with per-entry timestamps for max-age expiry."""

    def __init__(self, cache_dir: str, *, max_age_seconds: float = 0.0) -> None:
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
            logger.warning("failed to load Hato cache: %s (starting fresh)", exc)
            self._entries = {}

    def size(self) -> int:
        return len(self._entries)

    def get(self, service: str, media_type: str, id_: int) -> HatoMappingData | None:
        entry = self._entries.get(_cache_key(service, media_type, id_))
        if entry is None:
            return None
        if self.max_age_seconds > 0 and time.time() - entry.get("cached_at", 0) > self.max_age_seconds:
            return None
        return HatoMappingData.from_dict(entry.get("data"))

    def set(self, service: str, media_type: str, id_: int, data: HatoMappingData) -> None:
        self._entries[_cache_key(service, media_type, id_)] = {
            "data": data.to_dict(),
            "cached_at": time.time(),
        }
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._entries, indent=2)

        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix="hato-", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        self._dirty = False
        logger.debug("saved %d Hato cache entries to %s", len(self._entries), self.path)


class HatoApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        cache_dir: str | None = None,
        cache_max_age_seconds: float = 0.0,
        http_timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url or DEFAULT_BASE_URL
        self.http_timeout = http_timeout
        self.session = RetryableSession(max_retries=3)
        self.cache = (
            HatoCache(cache_dir, max_age_seconds=cache_max_age_seconds) if cache_dir else None
        )
        if self.cache is not None:
            logger.info("Hato cache loaded (%d entries)", self.cache.size())

    def get_anilist_id(self, mal_id: int, media_type: str) -> int | None:
        """mal_id -> AniList ID. media_type is "anime" or "manga"."""
        return self._lookup(service="mal", media_type=media_type, id_=mal_id, field="anilist_id")

    def get_mal_id(self, anilist_id: int, media_type: str) -> int | None:
        """anilist_id -> MAL ID. media_type is "anime" or "manga"."""
        return self._lookup(
            service="anilist", media_type=media_type, id_=anilist_id, field="mal_id"
        )

    def _lookup(self, *, service: str, media_type: str, id_: int, field: str) -> int | None:
        if self.cache is not None:
            cached = self.cache.get(service, media_type, id_)
            if cached is not None:
                value = getattr(cached, field)
                logger.debug("[HATO CACHE] hit: %s %s %d -> %s", service, media_type, id_, value)
                return value if value and value > 0 else None

        url = f"{self.base_url}/api/mappings/{service}/{media_type}/{id_}"
        data = self._request(url)
        if data is None:
            if self.cache is not None:
                self.cache.set(service, media_type, id_, HatoMappingData())
            return None

        if self.cache is not None:
            self.cache.set(service, media_type, id_, data)

        value = getattr(data, field)
        return value if value and value > 0 else None

    def _request(self, url: str) -> HatoMappingData | None:
        # Errors are non-fatal here: Hato is an optional best-effort fallback
        # in the id-mapping strategy chain, not a hard dependency. A timeout,
        # connection error, or exhausted retry must not crash the whole sync
        # run, so treat any request failure the same as "no mapping found".
        try:
            # Hato rejects requests without a browser-like User-Agent header.
            response = self.session.get(
                url, headers={"User-Agent": "Mozilla/5.0"}, timeout=self.http_timeout
            )
        except Exception as exc:  # noqa: BLE001 (deliberately non-fatal, see above)
            logger.debug("[HATO API] request error for %s: %s", url, exc)
            return None
        if response.status_code == 404:
            return None
        if not response.ok:
            logger.debug("[HATO API] unexpected status %d for %s", response.status_code, url)
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.debug("[HATO API] invalid JSON response from %s", url)
            return None
        return HatoMappingData.from_dict(payload.get("data"))

    def save_cache(self) -> None:
        if self.cache is not None:
            self.cache.save()
