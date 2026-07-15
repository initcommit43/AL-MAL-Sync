"""ID mapping strategy chain: manual -> direct -> offline DB -> online APIs -> title match -> API search.

Ported from the reference Go tool's strategies.go. Each strategy tries to find
the Target (existing list entry, or a freshly looked-up media entry) that
corresponds to a given Source. The chain tries them in order; the first match
wins.

Deliberate deviations from Go:
  - No context.Context or `prefix` string threaded through every call. Python's
    logging doesn't need a per-call context object, and the prefix was only
    used to decorate log lines (e.g. "AniList to MAL anime"); the module logger
    already gives that context.
  - `MediaService`/`MediaServiceWithMalId` are Protocols defined here (the
    consumer), not in sync/service.py (the not-yet-built Phase 6 provider).
    This is the same dependency direction Go uses (strategies.go depends on an
    interface, service.go implements it) - Phase 6's MediaService class will
    satisfy these Protocols structurally, no inheritance required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..models import Anime, Manga
from .jikan_api import find_best_jikan_match, match_jikan_manga_to_source, search_titles_for_jikan

if TYPE_CHECKING:
    from ..models import Source, Target
    from .arm_api import ArmApiClient
    from .hato_api import HatoApiClient
    from .jikan_api import JikanClient
    from .manual_mappings import MappingsConfig
    from .offline_database import OfflineDatabase

logger = logging.getLogger(__name__)


class Reporter(Protocol):
    """Sink for rejected-match warnings. Optional; strategies work without one."""

    def add_warning(self, title: str, reason: str, detail: str, media_type: str) -> None: ...


class MediaService(Protocol):
    def get_by_id(self, target_id: int) -> Target | None: ...
    def get_by_name(self, name: str) -> list[Target]: ...


class MediaServiceWithMalId(MediaService, Protocol):
    def get_by_mal_id(self, mal_id: int) -> Target | None: ...


@dataclass
class MatchResult:
    target: Target
    strategy_name: str
    strategy_idx: int  # position in chain; lower = higher priority (used for dedup)


class Strategy(Protocol):
    name: str

    def find_target(
        self,
        src: Source,
        existing_targets: dict[int, Target],
        reporter: Reporter | None = None,
    ) -> Target | None: ...


class StrategyChain:
    def __init__(self, strategies: list[Strategy]) -> None:
        self.strategies = strategies

    def find_target_with_meta(
        self,
        src: Source,
        existing_targets: dict[int, Target],
        reporter: Reporter | None = None,
    ) -> MatchResult | None:
        """Try each strategy in order; return the first match plus which
        strategy found it (the index is used later to resolve N:1 conflicts)."""
        for idx, strategy in enumerate(self.strategies):
            target = strategy.find_target(src, existing_targets, reporter)
            if target is not None:
                logger.debug(
                    "[%s] found target using strategy: %s", src.get_title(), strategy.name
                )
                return MatchResult(target=target, strategy_name=strategy.name, strategy_idx=idx)
        return None


def should_reject_match(
    src: Source, target: Target, reporter: Reporter | None = None
) -> bool:
    """Guard applied before accepting a title/API match (not needed for direct
    ID lookups, which are trusted by construction)."""
    src_id = src.get_target_id()
    tgt_id = target.get_target_id()
    if src_id > 0 and tgt_id > 0 and src_id != tgt_id:
        logger.debug(
            "rejecting match due to target ID mismatch: source %d, target %d", src_id, tgt_id
        )
        return True

    # The special-vs-series guard only applies to anime; Go's shouldRejectMatch
    # only type-asserts to Anime too, manga never gets this extra check.
    if not isinstance(src, Anime) or not isinstance(target, Anime):
        return False
    if not src.is_potentially_incorrect_match(target):
        return False

    reason = "unknown reason"
    if src.id_mal == 0 and target.id_mal > 0 and not src.identical_title_match(target):
        reason = "different titles (source has no MAL ID, target has different MAL ID)"
    elif src.num_episodes in (0, 1) and target.num_episodes > 4:
        reason = "episode count mismatch (special vs series)"

    if reporter is not None:
        reporter.add_warning(
            src.get_title(), reason, f"({src.num_episodes} vs {target.num_episodes})", "anime"
        )
    logger.debug("rejecting potentially incorrect match: %r - %s", src.get_title(), reason)
    return True


class ManualMappingStrategy:
    """User-defined AniList<->MAL mappings from mappings.yaml. Should be first
    in the chain: an explicit user override beats every automated guess."""

    name = "ManualMappingStrategy"

    def __init__(self, mappings: MappingsConfig | None, *, reverse: bool) -> None:
        self.mappings = mappings
        self.reverse = reverse

    def find_target(self, src, existing_targets, reporter=None):
        if self.mappings is None or not isinstance(src, Anime | Manga):
            return None

        target_id = self._lookup(src)
        if target_id is None:
            return None

        target = existing_targets.get(target_id)
        if target is not None:
            logger.debug("found target by manual mapping: ID %d -> %s", target_id, target.get_title())
        else:
            logger.debug("manual mapping found ID %d but not in user's list", target_id)
        return target

    def _lookup(self, src: Anime | Manga) -> int | None:
        if self.reverse:
            # MAL->AniList: source is a MAL entry with id_anilist=0 (see
            # Anime.from_mal_entry). Only the MAL ID is meaningful here.
            return self.mappings.get_manual_anilist_id(src.id_mal) if src.id_mal > 0 else None

        # AniList->MAL: source always has a real, positive id_anilist (it comes
        # straight from an AniList list entry), so this is the only lookup that
        # can ever succeed in the forward direction.
        if src.id_anilist > 0:
            return self.mappings.get_manual_mal_id(src.id_anilist)
        return None


class IDStrategy:
    """Direct lookup by target ID in the user's existing target list."""

    name = "IDStrategy"

    def find_target(self, src, existing_targets, reporter=None):
        target = existing_targets.get(src.get_target_id())
        if target is not None:
            logger.debug("found target by ID %d (direct lookup)", src.get_target_id())
        return target


class OfflineDatabaseStrategy:
    """Anime only: local anime-offline-database ID mapping."""

    name = "OfflineDatabaseStrategy"

    def __init__(self, database: OfflineDatabase | None) -> None:
        self.database = database

    def find_target(self, src, existing_targets, reporter=None):
        if self.database is None or not isinstance(src, Anime):
            return None

        target_id = self._lookup_id(src)
        if target_id is None:
            return None
        return existing_targets.get(target_id)

    def _lookup_id(self, src: Anime) -> int | None:
        if src.id_mal > 0:
            found = self.database.get_anilist_id(src.id_mal)
            if found is not None:
                return found
        if src.id_anilist > 0:
            found = self.database.get_mal_id(src.id_anilist)
            if found is not None:
                return found
        return None


class ARMAPIStrategy:
    """Anime only, opt-in: ARM API (https://arm.haglund.dev) ID mapping fallback."""

    name = "ARMAPIStrategy"

    def __init__(self, client: ArmApiClient | None) -> None:
        self.client = client

    def find_target(self, src, existing_targets, reporter=None):
        if self.client is None or not isinstance(src, Anime):
            return None

        target_id = self._lookup_id(src)
        if target_id is None:
            return None
        return existing_targets.get(target_id)

    def _lookup_id(self, src: Anime) -> int | None:
        if src.id_mal > 0:
            found = self.client.get_anilist_id(src.id_mal)
            if found is not None:
                return found
        if src.id_anilist > 0:
            found = self.client.get_mal_id(src.id_anilist)
            if found is not None:
                return found
        return None


class HatoAPIStrategy:
    """Anime and manga: Hato API ID mapping, enabled by default."""

    name = "HatoAPIStrategy"

    def __init__(self, client: HatoApiClient | None) -> None:
        self.client = client

    def find_target(self, src, existing_targets, reporter=None):
        if self.client is None:
            return None

        if isinstance(src, Anime):
            target_id = self._lookup(src.id_mal, src.id_anilist, "anime")
        elif isinstance(src, Manga):
            target_id = self._lookup(src.id_mal, src.id_anilist, "manga")
        else:
            return None

        if target_id is None:
            return None
        return existing_targets.get(target_id)

    def _lookup(self, mal_id: int, anilist_id: int, media_type: str) -> int | None:
        if mal_id > 0:
            found = self.client.get_anilist_id(mal_id, media_type)
            if found is not None:
                return found
        if anilist_id > 0:
            found = self.client.get_mal_id(anilist_id, media_type)
            if found is not None:
                return found
        return None


class TitleStrategy:
    """Exact then fuzzy title matching against the user's existing target list."""

    name = "TitleStrategy"

    def find_target(self, src, existing_targets, reporter=None):
        src_title = src.get_title()
        targets = sorted(existing_targets.values(), key=lambda t: t.get_title())

        for target in targets:
            if target.get_title() == src_title:
                logger.debug("found target by exact title match: %s", src_title)
                return target

        for target in targets:
            if src.same_title_with_target(target) and src.same_type_with_target(target):
                if should_reject_match(src, target, reporter):
                    continue
                logger.debug(
                    "found target by fuzzy title match: %r -> %r", src_title, target.get_title()
                )
                return target

        return None


class JikanAPIStrategy:
    """Manga only, opt-in: Jikan API ID mapping via title search."""

    name = "JikanAPIStrategy"

    def __init__(self, client: JikanClient | None) -> None:
        self.client = client

    def find_target(self, src, existing_targets, reporter=None):
        if self.client is None or not isinstance(src, Manga):
            return None

        if src.id_mal == 0 and src.id_anilist > 0:
            return self._find_mal_target(src, existing_targets)
        if src.id_anilist == 0 and src.id_mal > 0:
            return self._find_anilist_target(src, existing_targets)
        return None

    def _find_mal_target(self, src: Manga, existing_targets: dict[int, Target]) -> Target | None:
        for query in search_titles_for_jikan(src.title_en, src.title_romaji):
            results = self.client.search_manga(query)
            mal_id = find_best_jikan_match(results, src.title_en, src.title_jp, src.title_romaji)
            if mal_id > 0:
                return existing_targets.get(mal_id)
        return None

    def _find_anilist_target(
        self, src: Manga, existing_targets: dict[int, Target]
    ) -> Target | None:
        jikan_data = self.client.get_manga_by_mal_id(src.id_mal)
        if jikan_data is None:
            return None

        for target in existing_targets.values():
            if isinstance(target, Manga) and match_jikan_manga_to_source(
                jikan_data, target.title_en, target.title_jp, target.title_romaji
            ):
                return target
        return None


class MALIDStrategy:
    """Reverse direction only: search AniList directly by MAL ID."""

    name = "MALIDStrategy"

    def __init__(self, service: MediaServiceWithMalId) -> None:
        self.service = service

    def find_target(self, src, existing_targets, reporter=None):
        src_id = src.get_source_id()
        if src_id <= 0:
            return None

        target = self.service.get_by_mal_id(src_id)
        if target is None:
            return None

        # Prefer the copy already in the user's list (has real progress/score
        # to compare against); fall back to the freshly-looked-up target if
        # it's a genuinely new, not-yet-added entry.
        existing = existing_targets.get(target.get_target_id())
        return existing if existing is not None else target


class APISearchStrategy:
    """Last resort: hit the live API directly, by ID or by name search."""

    name = "APISearchStrategy"

    def __init__(self, service: MediaService) -> None:
        self.service = service

    def find_target(self, src, existing_targets, reporter=None):
        tgt_id = src.get_target_id()

        if tgt_id > 0:
            target = self.service.get_by_id(tgt_id)
            if target is None:
                return None
            existing = existing_targets.get(tgt_id)
            return existing if existing is not None else target

        for candidate in self.service.get_by_name(src.get_title()):
            existing = existing_targets.get(candidate.get_target_id())
            if existing is not None:
                if should_reject_match(src, existing, reporter):
                    continue
                if not src.same_title_with_target(existing):
                    continue
                return existing
            if src.same_type_with_target(candidate):
                return candidate

        return None
