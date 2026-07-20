"""Track the most recent completed sync run, for the GUI Dashboard's "last
sync" card. Persists to a JSON state file
(~/.config/al-mal-sync/state/sync_history.json), atomic write like
unmapped.py's state file / oauth.py's token file.

Not a log -- each successful run overwrites the previous entry outright, this
is "what happened last time", not a history of every run ever made.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import default_sync_history_path

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SyncHistoryEntry:
    finished_at: str
    per_kind: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"finished_at": self.finished_at, "per_kind": self.per_kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncHistoryEntry:
        return cls(
            finished_at=data.get("finished_at", ""),
            per_kind=data.get("per_kind") or {},
        )

    @classmethod
    def from_statistics(cls, stats: Any) -> SyncHistoryEntry:
        """Build an entry from a sync.statistics.SyncStatistics instance at
        the moment a run finishes."""
        per_kind = {
            s.media_type: {
                "updated": s.updated,
                "skipped": s.skipped,
                "dry_run": s.dry_run,
                "errors": s.errors,
                "unmatched": s.unmatched,
            }
            for s in stats.per_media_type
        }
        return cls(finished_at=_now_iso(), per_kind=per_kind)


def load_last_sync(path: str | Path | None = None) -> SyncHistoryEntry | None:
    """Load the last-sync entry from JSON. Missing or corrupt file is not an
    error, just "no sync recorded yet" (matches unmapped.py's load_unmapped_state)."""
    file_path = Path(path) if path is not None else Path(default_sync_history_path())

    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("failed to parse sync history file %s: %s (ignoring)", file_path, exc)
        return None

    return SyncHistoryEntry.from_dict(data)


def save_sync_history(entry: SyncHistoryEntry, path: str | Path | None = None) -> None:
    """Write to a temp file in the same directory, then atomically rename --
    same pattern as unmapped.py's save_unmapped_state."""
    file_path = Path(path) if path is not None else Path(default_sync_history_path())
    file_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(entry.to_dict(), indent=2)

    fd, tmp_name = tempfile.mkstemp(dir=file_path.parent, prefix="sync-history-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, file_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
