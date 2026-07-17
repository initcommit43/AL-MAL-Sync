"""Sync run statistics: per-media-type and aggregate total counts, formatted
as a plain-text summary table.

Ported from the reference Go tool's statistics.go. No table-formatting
dependency (no `rich`/`tabulate`) — the run only ever has one or two rows
(anime, manga) plus an optional total, so simple column-width alignment is
enough.
"""

from __future__ import annotations

from dataclasses import dataclass

from .updater import SyncOutcome


@dataclass
class MediaTypeStats:
    media_type: str
    total: int = 0
    updated: int = 0
    skipped: int = 0
    dry_run: int = 0
    errors: int = 0
    unmatched: int = 0

    @classmethod
    def from_outcome(cls, media_type: str, outcome: SyncOutcome) -> MediaTypeStats:
        updated = len(outcome.updated)
        skipped = len(outcome.skipped)
        dry_run = len(outcome.dry_run)
        errors = len(outcome.errors)
        unmatched = len(outcome.unmatched)
        return cls(
            media_type=media_type,
            total=updated + skipped + dry_run + errors + unmatched,
            updated=updated,
            skipped=skipped,
            dry_run=dry_run,
            errors=errors,
            unmatched=unmatched,
        )

@dataclass
class SyncStatistics:
    per_media_type: list[MediaTypeStats]

    @classmethod
    def from_outcomes(cls, outcomes: dict[str, SyncOutcome]) -> SyncStatistics:
        return cls([MediaTypeStats.from_outcome(kind, outcome) for kind, outcome in outcomes.items()])

    @property
    def total(self) -> MediaTypeStats:
        agg = MediaTypeStats(media_type="total")
        for stats in self.per_media_type:
            agg.total += stats.total
            agg.updated += stats.updated
            agg.skipped += stats.skipped
            agg.dry_run += stats.dry_run
            agg.errors += stats.errors
            agg.unmatched += stats.unmatched
        return agg


_HEADER = ("media type", "total", "updated", "skipped", "dry-run", "errors", "unmatched")


def _row(stats: MediaTypeStats) -> tuple[str, ...]:
    return (
        stats.media_type, str(stats.total), str(stats.updated),
        str(stats.skipped), str(stats.dry_run), str(stats.errors), str(stats.unmatched),
    )


def format_statistics_table(stats: SyncStatistics) -> str:
    rows = [_HEADER, *(_row(s) for s in stats.per_media_type)]
    if len(stats.per_media_type) > 1:
        rows.append(_row(stats.total))

    widths = [max(len(row[col]) for row in rows) for col in range(len(_HEADER))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
