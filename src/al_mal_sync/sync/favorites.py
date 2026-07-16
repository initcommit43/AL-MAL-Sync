"""Favorites sync (see docs/favorites-sync.md).

Ported from the reference Go tool's favorites.go. Asymmetric by construction:
MAL API v2 exposes no favorites write endpoint, so this only ever *writes*
AniList favorites (sourced from MAL's favorites via the Jikan API) and
*reports* AniList-only favorites MAL doesn't have -- there's nothing to write
them back to.

Runs as a separate phase after the main sync, reusing the id mappings the
anime/manga Updater runs already resolved (SyncOutcome.matched) rather than
re-running strategy matching here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import Anime, Manga

if TYPE_CHECKING:
    from ..clients.anilist import AniListClient
    from .conflict import ResolvedMatch

logger = logging.getLogger(__name__)


def build_id_mapping(matched: list[ResolvedMatch]) -> dict[int, int]:
    """Build a {source_id: target_id} map from one Updater run's resolved
    matches. Orientation follows the direction that run was for: a forward
    (AniList -> MAL) outcome yields {anilist_id: mal_id}, a reverse
    (MAL -> AniList) outcome yields {mal_id: anilist_id} -- source/target
    each report their own real id regardless of direction (see
    Source.get_source_id / Target.get_target_id)."""
    return {item.source.get_source_id(): item.match.target.get_target_id() for item in matched}


@dataclass
class FavoritesOutcome:
    added_to_anilist: list[int] = field(default_factory=list)
    already_favourited: list[int] = field(default_factory=list)
    # (anilist_id, mal_id) pairs favourited on AniList but not on MAL.
    mismatched: list[tuple[int, int]] = field(default_factory=list)
    unmapped: list[int] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


def sync_mal_favorites_to_anilist(
    mal_favorite_ids: set[int],
    anilist_targets: dict[int, Anime | Manga],
    mal_to_anilist: dict[int, int],
    anilist_client: AniListClient,
    *,
    media_kind: str,
) -> FavoritesOutcome:
    """MAL -> AniList: add every MAL favorite that isn't already an AniList
    favorite. Never removes an AniList-only favorite -- AniList's
    ToggleFavourite flips state on every call, so an entry already favourited
    is skipped rather than toggled off.
    """
    outcome = FavoritesOutcome()
    for mal_id in sorted(mal_favorite_ids):
        anilist_id = mal_to_anilist.get(mal_id)
        if anilist_id is None:
            outcome.unmapped.append(mal_id)
            continue

        target = anilist_targets.get(anilist_id)
        if target is not None and target.is_favourite:
            outcome.already_favourited.append(anilist_id)
            continue

        try:
            if media_kind == "anime":
                anilist_client.toggle_favourite(anime_id=anilist_id)
            else:
                anilist_client.toggle_favourite(manga_id=anilist_id)
        except Exception as exc:
            outcome.errors.append((anilist_id, str(exc)))
            logger.warning("failed to favorite AniList id %d: %s", anilist_id, exc)
            continue

        outcome.added_to_anilist.append(anilist_id)

    return outcome


def check_anilist_favorites_against_mal(
    anilist_entries: list[Anime | Manga],
    mal_favorite_ids: set[int],
    anilist_to_mal: dict[int, int],
) -> FavoritesOutcome:
    """AniList -> MAL: report AniList favorites missing on MAL. Report-only --
    there's no MAL API v2 endpoint to write favorites back to."""
    outcome = FavoritesOutcome()
    for entry in anilist_entries:
        if not isinstance(entry, Anime | Manga) or not entry.is_favourite:
            continue

        mal_id = anilist_to_mal.get(entry.id_anilist)
        if mal_id is None:
            outcome.unmapped.append(entry.id_anilist)
        elif mal_id not in mal_favorite_ids:
            outcome.mismatched.append((entry.id_anilist, mal_id))

    return outcome
