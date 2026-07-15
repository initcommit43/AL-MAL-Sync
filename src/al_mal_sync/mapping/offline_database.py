"""Local anime-offline-database lookup for anime ID mapping.

Ported from the reference Go tool's offline_database.go: downloads/caches the
minified JSON dataset from
https://github.com/manami-project/anime-offline-database and builds
AniList<->MAL ID lookup tables from each entry's "sources" URLs.

Deliberate simplification: Go streams the JSON to avoid loading the whole file
into memory. Python's stdlib json module has no streaming decoder without an
extra dependency (ijson), and the dataset is small enough (tens of MB) that a
plain json.load() is fine for a desktop sync tool.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..http_retry import RetryableSession

logger = logging.getLogger(__name__)

GITHUB_RELEASES_API = (
    "https://api.github.com/repos/manami-project/anime-offline-database/releases/latest"
)
ASSET_NAME = "anime-offline-database-minified.json"
METADATA_FILE = "version.txt"
DATABASE_FILE = "anime-offline-database.json"

_MAL_URL_PREFIX = "https://myanimelist.net/anime/"
_ANILIST_URL_PREFIX = "https://anilist.co/anime/"


class OfflineDatabaseError(Exception):
    """Raised when the offline database can't be downloaded and no cache exists."""


@dataclass
class AODEntry:
    sources: list[str] = field(default_factory=list)
    title: str = ""
    type: str = ""


class OfflineDatabase:
    def __init__(self) -> None:
        self._mal_to_anilist: dict[int, int] = {}
        self._anilist_to_mal: dict[int, int] = {}
        self.last_update: str = ""
        self.entries: int = 0

    def get_anilist_id(self, mal_id: int) -> int | None:
        return self._mal_to_anilist.get(mal_id)

    def get_mal_id(self, anilist_id: int) -> int | None:
        return self._anilist_to_mal.get(anilist_id)

    def _index_entry(self, entry: AODEntry) -> None:
        mal_id = 0
        anilist_id = 0
        for source in entry.sources:
            found = _extract_id_from_url(source, _MAL_URL_PREFIX)
            if found is not None:
                mal_id = found
            found = _extract_id_from_url(source, _ANILIST_URL_PREFIX)
            if found is not None:
                anilist_id = found

        if mal_id > 0 and anilist_id > 0:
            self._mal_to_anilist[mal_id] = anilist_id
            self._anilist_to_mal[anilist_id] = mal_id
            self.entries += 1

    @classmethod
    def build_from_entries(cls, entries: list[AODEntry]) -> OfflineDatabase:
        """Build directly from entries, bypassing file I/O. Used in tests."""
        db = cls()
        for entry in entries:
            db._index_entry(entry)
        return db


def _extract_id_from_url(url: str, prefix: str) -> int | None:
    """Extract a numeric ID from a URL with the given prefix.

    Example: _extract_id_from_url("https://myanimelist.net/anime/1535/title", prefix)
    returns 1535.
    """
    if not url.startswith(prefix):
        return None
    rest = url[len(prefix) :]
    slash = rest.find("/")
    if slash != -1:
        rest = rest[:slash]
    if rest.isdigit() and int(rest) > 0:
        return int(rest)
    return None


def load_offline_database(
    cache_dir: str,
    *,
    auto_update: bool = True,
    force_refresh: bool = False,
    http_timeout: float = 30.0,
) -> OfflineDatabase:
    """Load the offline database, downloading or updating it as needed."""
    cache_path = Path(cache_dir)
    db_path = cache_path / DATABASE_FILE
    meta_path = cache_path / METADATA_FILE

    exists = db_path.exists()
    if force_refresh or not exists:
        try:
            _download_and_cache(cache_path, db_path, meta_path, http_timeout)
        except Exception as exc:
            if not exists:
                raise OfflineDatabaseError(f"download offline database: {exc}") from exc
            logger.warning(
                "failed to download offline database: %s (using cached version)", exc
            )
    elif auto_update:
        _update_if_needed(db_path, meta_path, http_timeout)

    return _parse_aod_file(db_path)


def _download_and_cache(cache_dir: Path, db_path: Path, meta_path: Path, timeout: float) -> None:
    download_url, tag = _get_latest_release_info(timeout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _download_file(download_url, db_path, timeout)
    try:
        meta_path.write_text(tag, encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to save offline database version metadata: %s", exc)
    logger.info("downloaded offline database (version %s)", tag)


def _update_if_needed(db_path: Path, meta_path: Path, timeout: float) -> None:
    cached_version = _get_cached_version(meta_path)
    try:
        latest_url, latest_tag = _get_latest_release_info(timeout)
    except Exception as exc:
        logger.debug(
            "cannot check for offline database updates: %s (using cached version)", exc
        )
        return

    if cached_version == latest_tag:
        logger.debug("offline database is up to date (version %s)", cached_version)
        return

    logger.info("updating offline database: %s -> %s", cached_version, latest_tag)
    try:
        _download_file(latest_url, db_path, timeout)
    except Exception as exc:
        logger.warning("failed to update offline database: %s (using cached version)", exc)
        return

    try:
        meta_path.write_text(latest_tag, encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to save offline database version metadata: %s", exc)


def _download_file(url: str, dest_path: Path, timeout: float) -> None:
    """Download to a temp file in the same directory, then atomically rename."""
    session = RetryableSession(max_retries=3)
    response = session.get(url, timeout=timeout, stream=True)
    if not response.ok:
        raise OfflineDatabaseError(f"unexpected status downloading offline database: {response.status_code}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, prefix="aod-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
        os.replace(tmp_path, dest_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _get_latest_release_info(timeout: float) -> tuple[str, str]:
    session = RetryableSession(max_retries=3)
    response = session.get(
        GITHUB_RELEASES_API,
        headers={"Accept": "application/vnd.github.v3+json"},
        timeout=timeout,
    )
    if not response.ok:
        raise OfflineDatabaseError(f"github API status: {response.status_code}")

    release: dict[str, Any] = response.json()
    for asset in release.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            return asset["browser_download_url"], release.get("tag_name", "")
    raise OfflineDatabaseError(f"asset {ASSET_NAME} not found in latest release")


def _get_cached_version(meta_path: Path) -> str:
    try:
        return meta_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _parse_aod_file(db_path: Path) -> OfflineDatabase:
    try:
        with db_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise OfflineDatabaseError(f"offline database file not found: {db_path}") from exc
    except json.JSONDecodeError as exc:
        raise OfflineDatabaseError(f"failed to parse offline database: {exc}") from exc

    db = OfflineDatabase()
    db.last_update = raw.get("lastUpdate", "")
    for item in raw.get("data") or []:
        entry = AODEntry(
            sources=list(item.get("sources") or []),
            title=item.get("title", ""),
            type=item.get("type", ""),
        )
        db._index_entry(entry)  # noqa: SLF001 (same module's own helper)
    return db
