"""Duplicate-target resolution: when more than one source entry resolves to
the same target, keep the highest-priority match and record the rest as
conflicts for the sync report.

Kept separate from the pipeline driver (sync/updater.py) so it's independently
testable, per PLAN.md Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..mapping.strategies import MatchResult
    from ..models import Source


@dataclass
class ResolvedMatch:
    source: Source
    match: MatchResult


@dataclass
class Conflict:
    """A losing source/match pair for a target that was already claimed by a
    higher-priority (or tie-broken) match."""

    source: Source
    target_id: int
    winner_source: Source
    strategy_name: str


def resolve_duplicates(
    matches: list[tuple[Source, MatchResult]],
) -> tuple[list[ResolvedMatch], list[Conflict]]:
    """Group `matches` by target id. When more than one source claims the same
    target, keep the match found by the highest-priority strategy (lowest
    `strategy_idx`); ties are broken in favor of an exact title match, then by
    whichever source came first. Everything else is recorded as a Conflict.
    """
    by_target: dict[int, list[tuple[Source, MatchResult]]] = {}
    for source, match in matches:
        by_target.setdefault(match.target.get_target_id(), []).append((source, match))

    resolved: list[ResolvedMatch] = []
    conflicts: list[Conflict] = []

    for target_id, claimants in by_target.items():
        if len(claimants) == 1:
            source, match = claimants[0]
            resolved.append(ResolvedMatch(source, match))
            continue

        ordered = sorted(claimants, key=_priority_key)
        winner_source, winner_match = ordered[0]
        resolved.append(ResolvedMatch(winner_source, winner_match))
        for source, match in ordered[1:]:
            conflicts.append(
                Conflict(
                    source=source,
                    target_id=target_id,
                    winner_source=winner_source,
                    strategy_name=match.strategy_name,
                )
            )

    return resolved, conflicts


def _priority_key(item: tuple[Source, MatchResult]) -> tuple[int, int]:
    source, match = item
    exact_title = 0 if source.get_title() == match.target.get_title() else 1
    return (match.strategy_idx, exact_title)
