"""Track and interactively resolve entries the mapping strategies couldn't
match. Ported from the reference Go tool's unmapped.go.

Persists to a JSON state file (~/.config/al-mal-sync/state/unmapped.json),
atomic write like offline_database.py's cache / oauth.py's token file. Each
sync run replaces only its own (media_type, direction) slice of entries --
this is a snapshot of "what's unmapped as of the last run of each kind", not
an accumulating log, so a full sync (anime + manga, both directions) needs
one call per Updater run to build up the complete picture.

Not to be confused with sync/updater.py's UnmatchedEntry, which wraps a live
Source object mid-pipeline; this module's UnmappedRecord is the flattened,
JSON-serializable form written to disk once a run finishes.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import default_unmapped_state_path
from .models import Anime, Manga

if TYPE_CHECKING:
    from .sync.updater import UnmatchedEntry as PipelineUnmatchedEntry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class UnmappedRecord:
    title: str
    anilist_id: int
    mal_id: int
    media_type: str  # "anime" | "manga"
    direction: str  # "forward" (AniList -> MAL) | "reverse" (MAL -> AniList)
    reason: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "anilist_id": self.anilist_id,
            "mal_id": self.mal_id,
            "media_type": self.media_type,
            "direction": self.direction,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnmappedRecord:
        return cls(
            title=data.get("title", ""),
            anilist_id=data.get("anilist_id", 0),
            mal_id=data.get("mal_id", 0),
            media_type=data.get("media_type", ""),
            direction=data.get("direction", ""),
            reason=data.get("reason", ""),
            updated_at=data.get("updated_at", ""),
        )

    @classmethod
    def from_pipeline_entry(
        cls, entry: PipelineUnmatchedEntry, *, media_type: str, direction: str
    ) -> UnmappedRecord:
        """Build a persisted record from sync/updater.py's transient
        UnmatchedEntry (wraps a live Source), at the moment a run finishes."""
        source = entry.source
        anilist_id = source.id_anilist if isinstance(source, Anime | Manga) else 0
        mal_id = source.id_mal if isinstance(source, Anime | Manga) else 0
        return cls(
            title=source.get_title(),
            anilist_id=anilist_id,
            mal_id=mal_id,
            media_type=media_type,
            direction=direction,
            reason=entry.reason,
            updated_at=_now_iso(),
        )


@dataclass
class UnmappedState:
    entries: list[UnmappedRecord] = field(default_factory=list)

    def replace_run(self, media_type: str, direction: str, records: list[UnmappedRecord]) -> None:
        """Drop this (media_type, direction)'s previous entries and replace
        them with the latest run's results."""
        self.entries = [
            e for e in self.entries if not (e.media_type == media_type and e.direction == direction)
        ]
        self.entries.extend(records)

    def remove_by_anilist_id(self, anilist_id: int) -> None:
        self.entries = [e for e in self.entries if e.anilist_id != anilist_id]

    def remove_by_mal_id(self, mal_id: int) -> None:
        self.entries = [e for e in self.entries if e.mal_id != mal_id]

    def remove_by_title(self, title: str) -> None:
        lowered = title.lower()
        self.entries = [e for e in self.entries if e.title.lower() != lowered]

    def clear(self) -> None:
        self.entries = []


def load_unmapped_state(path: str | Path | None = None) -> UnmappedState:
    """Load unmapped state from JSON. Missing or corrupt file is not an
    error, just an empty state (matches mappings.yaml's load_mappings)."""
    file_path = Path(path) if path is not None else Path(default_unmapped_state_path())

    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return UnmappedState()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("failed to parse unmapped state file %s: %s (starting fresh)", file_path, exc)
        return UnmappedState()

    entries = [UnmappedRecord.from_dict(item) for item in data.get("entries") or []]
    return UnmappedState(entries=entries)


def save_unmapped_state(state: UnmappedState, path: str | Path | None = None) -> None:
    """Write to a temp file in the same directory, then atomically rename --
    same pattern as offline_database.py's download and oauth.py's token
    file, avoids a corrupt/truncated state file on crash mid-write."""
    file_path = Path(path) if path is not None else Path(default_unmapped_state_path())
    file_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps({"entries": [e.to_dict() for e in state.entries]}, indent=2)

    fd, tmp_name = tempfile.mkstemp(dir=file_path.parent, prefix="unmapped-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, file_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
