"""ARM API client (anime-only ID mapping fallback).

Ported from the reference Go tool's arm_api.go. No caching layer, unlike Hato/
Jikan below, since the Go tool doesn't cache ARM responses either (ARM is the
lowest-priority, opt-in fallback in the strategy chain, so it's called least often).
"""

from __future__ import annotations

import logging
from typing import Any

from ..http_retry import RetryableSession

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://arm.haglund.dev"


class ArmApiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, http_timeout: float = 30.0) -> None:
        self.base_url = base_url or DEFAULT_BASE_URL
        self.http_timeout = http_timeout
        self.session = RetryableSession(max_retries=3)

    def get_anilist_id(self, mal_id: int) -> int | None:
        """Return the AniList ID for a given MAL ID, or None if not mapped."""
        data = self._request(source="myanimelist", id_=mal_id, include="anilist")
        if data is None:
            return None
        return data.get("anilist")

    def get_mal_id(self, anilist_id: int) -> int | None:
        """Return the MAL ID for a given AniList ID, or None if not mapped."""
        data = self._request(source="anilist", id_=anilist_id, include="myanimelist")
        if data is None:
            return None
        return data.get("myanimelist")

    def _request(self, *, source: str, id_: int, include: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/v2/ids"
        response = self.session.get(
            url,
            params={"source": source, "id": id_, "include": include},
            timeout=self.http_timeout,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            logger.debug("[ARM API] unexpected status %d for %s", response.status_code, url)
            return None
        try:
            return response.json()
        except ValueError:
            logger.debug("[ARM API] invalid JSON response from %s", url)
            return None
