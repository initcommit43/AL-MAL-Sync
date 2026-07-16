"""Top-level sync orchestration: resolve matches, deduplicate, then apply
updates. Ported from the reference Go tool's updater.go 3-phase pipeline.

Runs once per media type (anime/manga) per direction. The caller is
responsible for fetching `sources` and `existing_targets` from the two
services and wiring up the right StrategyChain/target MediaService for that
direction (see PLAN.md Phase 6) -- this module only drives the pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..mapping.strategies import MatchResult
from ..models import Anime, Manga
from .conflict import Conflict, resolve_duplicates

if TYPE_CHECKING:
    from ..mapping.manual_mappings import MappingsConfig
    from ..mapping.strategies import StrategyChain
    from ..models import Source, Target
    from .conflict import ResolvedMatch
    from .service import (
        AniListAnimeService,
        AniListMangaService,
        MyAnimeListAnimeService,
        MyAnimeListMangaService,
    )

    TargetService = (
        AniListAnimeService | AniListMangaService | MyAnimeListAnimeService | MyAnimeListMangaService
    )

logger = logging.getLogger(__name__)


@dataclass
class UnmatchedEntry:
    source: Source
    reason: str = "no strategy matched"


@dataclass
class SyncOutcome:
    updated: list[Source] = field(default_factory=list)
    skipped: list[Source] = field(default_factory=list)
    dry_run: list[Source] = field(default_factory=list)
    errors: list[tuple[Source, str]] = field(default_factory=list)
    unmatched: list[UnmatchedEntry] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Every source/target pair that survived dedup, win or lose on the
    # progress-sync outcome above -- sync/favorites.py reuses these id
    # mappings instead of re-running strategy matching.
    matched: list[ResolvedMatch] = field(default_factory=list)


class _WarningCollector:
    """Adapts strategies.Reporter to a plain list of formatted strings."""

    def __init__(self, warnings: list[str]) -> None:
        self._warnings = warnings

    def add_warning(self, title: str, reason: str, detail: str, media_type: str) -> None:
        self._warnings.append(f"[{media_type}] {title!r}: {reason} {detail}")


class Updater:
    """Runs the resolve -> deduplicate -> process pipeline for one media
    type/direction."""

    def __init__(
        self,
        chain: StrategyChain,
        target_service: TargetService,
        *,
        mappings: MappingsConfig | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.chain = chain
        self.target_service = target_service
        self.mappings = mappings
        self.force = force
        self.dry_run = dry_run

    def run(self, sources: list[Source], existing_targets: dict[int, Target]) -> SyncOutcome:
        outcome = SyncOutcome()

        # Deterministic, readable progress output: group by status, then title.
        ordered_sources = sorted(sources, key=lambda s: (s.get_status_string(), s.get_title()))

        if self.force:
            matches = self._resolve_forced(ordered_sources, existing_targets, outcome)
        else:
            matches = self._resolve(ordered_sources, existing_targets, outcome)

        resolved, outcome.conflicts = resolve_duplicates(matches)
        outcome.matched = resolved

        for item in resolved:
            self._process(item.source, item.match.target, outcome)

        return outcome

    def _resolve(
        self,
        sources: list[Source],
        existing_targets: dict[int, Target],
        outcome: SyncOutcome,
    ) -> list[tuple[Source, MatchResult]]:
        reporter = _WarningCollector(outcome.warnings)
        matches: list[tuple[Source, MatchResult]] = []
        for source in sources:
            if self._is_ignored(source):
                continue
            result = self.chain.find_target_with_meta(source, existing_targets, reporter)
            if result is None:
                outcome.unmatched.append(UnmatchedEntry(source))
                continue
            matches.append((source, result))
        return matches

    def _resolve_forced(
        self,
        sources: list[Source],
        existing_targets: dict[int, Target],
        outcome: SyncOutcome,
    ) -> list[tuple[Source, MatchResult]]:
        """--force: skip strategy matching entirely, sync every source
        directly by id."""
        matches: list[tuple[Source, MatchResult]] = []
        for source in sources:
            if self._is_ignored(source):
                continue
            target_id = source.get_target_id()
            target = existing_targets.get(target_id) if target_id > 0 else None
            if target is None:
                outcome.unmatched.append(
                    UnmatchedEntry(source, reason="--force: no target with matching ID")
                )
                continue
            matches.append((source, MatchResult(target=target, strategy_name="Forced", strategy_idx=-1)))
        return matches

    def _is_ignored(self, source: Source) -> bool:
        if self.mappings is None or not isinstance(source, Anime | Manga):
            return False
        if source.is_reverse:
            return self.mappings.is_ignored_by_mal_id(source.id_mal)
        return self.mappings.is_ignored(source.id_anilist, source.get_title())

    def _process(self, source: Source, target: Target, outcome: SyncOutcome) -> None:
        if source.same_progress_with_target(target):
            outcome.skipped.append(source)
            return

        if self.dry_run:
            outcome.dry_run.append(source)
            logger.info(
                "[dry-run] would update %r: %s",
                source.get_title(),
                source.get_string_diff_with_target(target),
            )
            return

        try:
            # Broad catch by design: this is the per-entry boundary of the sync
            # run. Either service can raise its own API error type, and one
            # failing entry (rate limit, transient network error, etc.) must
            # not abort updates for every other entry already resolved.
            self.target_service.update(source, target.get_target_id())
        except Exception as exc:
            outcome.errors.append((source, str(exc)))
            logger.warning("failed to update %r: %s", source.get_title(), exc)
            return

        outcome.updated.append(source)
