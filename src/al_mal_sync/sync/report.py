"""Sync run report: accumulated warnings (rejected matches with reason),
duplicate-target conflicts, unmapped-entry count, and favorites mismatches --
printed after the statistics table.

Ported from the reference Go tool's report.go (SyncReport). Kept separate
from statistics.py (counts) since this is qualitative detail the user acts
on (e.g. "run `unmapped --fix`"), not a summary number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conflict import Conflict
    from .favorites import FavoritesOutcome
    from .updater import SyncOutcome


@dataclass
class SyncReport:
    warnings: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    unmapped_count: int = 0
    # (media_type, anilist_id, mal_id) favorited on AniList but missing on MAL.
    favorites_mismatched: list[tuple[str, int, int]] = field(default_factory=list)
    # (media_type, anilist_id, error message) from failed AniList favorite writes.
    favorites_errors: list[tuple[str, int, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.warnings or self.conflicts or self.unmapped_count
            or self.favorites_mismatched or self.favorites_errors
        )


def build_report(
    outcomes: dict[str, SyncOutcome],
    favorites_outcomes: dict[str, FavoritesOutcome] | None = None,
) -> SyncReport:
    """Aggregate one run's per-media-type SyncOutcomes (and optional
    favorites results) into a single report."""
    report = SyncReport()
    for outcome in outcomes.values():
        report.warnings.extend(outcome.warnings)
        report.conflicts.extend(outcome.conflicts)
        report.unmapped_count += len(outcome.unmatched)

    for media_type, favorites_outcome in (favorites_outcomes or {}).items():
        report.favorites_mismatched.extend(
            (media_type, anilist_id, mal_id) for anilist_id, mal_id in favorites_outcome.mismatched
        )
        report.favorites_errors.extend(
            (media_type, anilist_id, message) for anilist_id, message in favorites_outcome.errors
        )

    return report


def format_report(report: SyncReport) -> str:
    if report.is_empty():
        return "No warnings, conflicts, or mismatches."

    lines: list[str] = []

    if report.warnings:
        lines.append(f"Warnings ({len(report.warnings)}):")
        lines.extend(f"  - {warning}" for warning in report.warnings)

    if report.conflicts:
        lines.append(f"Conflicts ({len(report.conflicts)}):")
        for conflict in report.conflicts:
            lines.append(
                f"  - {conflict.source.get_title()!r} lost target ID {conflict.target_id} to "
                f"{conflict.winner_source.get_title()!r} (matched via {conflict.strategy_name})"
            )

    if report.unmapped_count:
        lines.append(f"Unmapped entries: {report.unmapped_count} (see `al-mal-sync unmapped`)")

    if report.favorites_mismatched:
        lines.append(f"Favorites missing on MAL ({len(report.favorites_mismatched)}):")
        lines.extend(
            f"  - [{media_type}] AniList ID {anilist_id} (MAL ID {mal_id})"
            for media_type, anilist_id, mal_id in report.favorites_mismatched
        )

    if report.favorites_errors:
        lines.append(f"Favorites errors ({len(report.favorites_errors)}):")
        lines.extend(
            f"  - [{media_type}] AniList ID {anilist_id}: {message}"
            for media_type, anilist_id, message in report.favorites_errors
        )

    return "\n".join(lines)
