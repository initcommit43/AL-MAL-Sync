"""Sync orchestration: wiring the OAuth-backed API clients, the id-mapping
strategy chain, and updater.py's Updater together for a given (media kind,
direction) pair -- none of the lower-level modules know about each other, so
something has to assemble them. This module is the shared entry point for
both the CLI and any other frontend (e.g. the GUI): it has no click
dependency and returns structured results instead of printing them, so
callers decide how to present progress/results themselves.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..clients.anilist import AniListAPIError, AniListClient
from ..clients.myanimelist import MyAnimeListClient
from ..config import Config, parse_duration
from ..mapping.arm_api import ArmApiClient
from ..mapping.hato_api import HatoApiClient
from ..mapping.jikan_api import JikanApiError, JikanClient
from ..mapping.manual_mappings import MappingsConfig, load_mappings
from ..mapping.offline_database import OfflineDatabase, OfflineDatabaseError, load_offline_database
from ..mapping.strategies import (
    APISearchStrategy,
    ARMAPIStrategy,
    HatoAPIStrategy,
    IDStrategy,
    JikanAPIStrategy,
    MALIDStrategy,
    ManualMappingStrategy,
    OfflineDatabaseStrategy,
    StrategyChain,
    TitleStrategy,
)
from ..models import Anime, Manga
from ..oauth import create_anilist_oauth, create_myanimelist_oauth
from ..sync_history import SyncHistoryEntry, save_sync_history
from ..unmapped import UnmappedRecord, load_unmapped_state, save_unmapped_state
from .favorites import (
    FavoritesOutcome,
    build_id_mapping,
    check_anilist_favorites_against_mal,
    sync_mal_favorites_to_anilist,
)
from .service import (
    AniListAnimeService,
    AniListMangaService,
    MyAnimeListAnimeService,
    MyAnimeListMangaService,
)
from .statistics import SyncStatistics
from .updater import SyncOutcome, Updater

logger = logging.getLogger(__name__)

# Called as on_kind_start(kind, reverse) once per media kind before it starts,
# and on_progress(current, total) once per resolved match while it's being
# written -- both optional, so callers that don't care about live progress
# (e.g. tests) can omit them.
OnKindStart = Callable[[str, bool], None]
OnProgress = Callable[[int, int], None]


def build_strategy_chain(
    kind: str,
    *,
    reverse: bool,
    mappings: MappingsConfig,
    offline_database: OfflineDatabase | None,
    hato_client: HatoApiClient | None,
    arm_client: ArmApiClient | None,
    jikan_client: JikanClient | None,
    target_service: Any,
) -> StrategyChain:
    """Assemble the strategy chain for one media kind/direction, in the fixed
    priority order from PLAN.md Phase 5. `target_service` doubles as the
    MediaServiceWithMalId for MALIDStrategy (reverse only, since only
    AniList's client implements get_by_mal_id) and the MediaService for the
    last-resort APISearchStrategy."""
    strategies: list[Any] = [
        ManualMappingStrategy(mappings, reverse=reverse),
        IDStrategy(),
    ]
    if kind == "anime":
        strategies.append(OfflineDatabaseStrategy(offline_database))
    strategies.append(HatoAPIStrategy(hato_client))
    if kind == "anime":
        strategies.append(ARMAPIStrategy(arm_client))
    strategies.append(TitleStrategy())
    if kind == "manga":
        strategies.append(JikanAPIStrategy(jikan_client))
    if reverse:
        strategies.append(MALIDStrategy(target_service))
    strategies.append(APISearchStrategy(target_service))
    return StrategyChain(strategies)


def _invert(mapping: dict[int, int]) -> dict[int, int]:
    return {v: k for k, v in mapping.items()}


def run_sync(
    config: Config,
    *,
    force: bool,
    dry_run: bool,
    manga: bool,
    all_media: bool,
    reverse: bool,
    offline_db: bool,
    offline_db_force_refresh: bool,
    arm_api: bool,
    arm_api_url: str | None,
    jikan_api: bool,
    favorites: bool,
    on_kind_start: OnKindStart | None = None,
    on_progress: OnProgress | None = None,
) -> tuple[dict[str, SyncOutcome], dict[str, FavoritesOutcome]]:
    """Run one sync pass (all requested media kinds) and return the raw
    outcomes for the caller to summarize/report however it likes -- the CLI
    prints them via sync/statistics.py and sync/report.py; a GUI would bind
    them to a results panel instead."""
    media_kinds = ["anime", "manga"] if all_media else (["manga"] if manga else ["anime"])
    if favorites:
        jikan_api = True

    mappings = load_mappings(config.resolved_mappings_file_path)

    anilist_oauth = create_anilist_oauth(config)
    mal_oauth = create_myanimelist_oauth(config)

    http_timeout = config.get_http_timeout().total_seconds()
    anilist_client = AniListClient(anilist_oauth, config.anilist.username, http_timeout=http_timeout)
    mal_client = MyAnimeListClient(mal_oauth, config.myanimelist.username, http_timeout=http_timeout)

    try:
        score_format = anilist_client.get_user_score_format()
    except AniListAPIError as exc:
        raise AniListAPIError(
            f"failed to fetch AniList score format: {exc}", status_code=getattr(exc, "status_code", None)
        ) from exc

    offline_database: OfflineDatabase | None = None
    if "anime" in media_kinds and (config.offline_database.enabled or offline_db):
        try:
            offline_database = load_offline_database(
                config.resolved_offline_db_cache_dir,
                auto_update=config.offline_database.auto_update,
                force_refresh=offline_db_force_refresh,
                http_timeout=http_timeout,
            )
        except OfflineDatabaseError as exc:
            logger.warning("offline database unavailable: %s", exc)

    hato_client: HatoApiClient | None = None
    if config.hato_api.enabled:
        hato_client = HatoApiClient(
            config.hato_api.base_url,
            cache_dir=config.resolved_hato_cache_dir,
            cache_max_age_seconds=parse_duration(config.hato_api.cache_max_age).total_seconds(),
            http_timeout=http_timeout,
        )

    arm_client: ArmApiClient | None = None
    if "anime" in media_kinds and (config.arm_api.enabled or arm_api):
        arm_client = ArmApiClient(arm_api_url or config.arm_api.base_url, http_timeout=http_timeout)

    jikan_client: JikanClient | None = None
    if config.jikan_api.enabled or jikan_api:
        jikan_client = JikanClient(
            config.resolved_jikan_cache_dir,
            cache_max_age_seconds=parse_duration(config.jikan_api.cache_max_age).total_seconds(),
            http_timeout=http_timeout,
        )

    try:
        media_state: dict[str, tuple[SyncOutcome, list[Any]]] = {}
        for kind in media_kinds:
            if on_kind_start is not None:
                on_kind_start(kind, reverse)
            outcome, anilist_entries, _target_service = _sync_one_kind(
                kind,
                reverse=reverse,
                score_format=score_format,
                anilist_client=anilist_client,
                mal_client=mal_client,
                mappings=mappings,
                offline_database=offline_database,
                hato_client=hato_client,
                arm_client=arm_client,
                jikan_client=jikan_client,
                force=force,
                dry_run=dry_run,
                on_progress=on_progress,
            )
            media_state[kind] = (outcome, anilist_entries)
            _persist_unmapped(config, kind, reverse, outcome)

        favorites_outcomes: dict[str, FavoritesOutcome] = {}
        if favorites:
            favorites_outcomes = _sync_favorites(
                config, media_state, reverse=reverse, jikan_client=jikan_client, anilist_client=anilist_client
            )

        outcomes = {kind: outcome for kind, (outcome, _entries) in media_state.items()}
        _persist_sync_history(config, outcomes)
        return outcomes, favorites_outcomes
    finally:
        if hato_client is not None:
            hato_client.save_cache()
        if jikan_client is not None:
            jikan_client.save_cache()


def _sync_one_kind(
    kind: str,
    *,
    reverse: bool,
    score_format: str,
    anilist_client: AniListClient,
    mal_client: MyAnimeListClient,
    mappings: MappingsConfig,
    offline_database: OfflineDatabase | None,
    hato_client: HatoApiClient | None,
    arm_client: ArmApiClient | None,
    jikan_client: JikanClient | None,
    force: bool,
    dry_run: bool,
    on_progress: OnProgress | None = None,
) -> tuple[SyncOutcome, list[Any], Any]:
    """Fetch both lists, build the source/target split for one (kind,
    direction) pair, and run the Updater. Returns the outcome, the list of
    AniList-side entries (needed by favorites sync regardless of direction,
    since is_favourite only exists on AniList), and the target service."""
    model = Anime if kind == "anime" else Manga

    if reverse:
        anilist_entries = [
            model.from_anilist_entry(e, score_format, reverse=True)
            for e in (anilist_client.get_user_anime_list() if kind == "anime" else anilist_client.get_user_manga_list())
        ]
        sources = [
            model.from_mal_entry(e, reverse=True)
            for e in (mal_client.get_user_anime_list() if kind == "anime" else mal_client.get_user_manga_list())
        ]
        target_service = (
            AniListAnimeService(anilist_client, score_format, reverse=True)
            if kind == "anime"
            else AniListMangaService(anilist_client, score_format, reverse=True)
        )
        existing = {t.get_target_id(): t for t in anilist_entries}
    else:
        anilist_entries = [
            model.from_anilist_entry(e, score_format, reverse=False)
            for e in (anilist_client.get_user_anime_list() if kind == "anime" else anilist_client.get_user_manga_list())
        ]
        sources = anilist_entries
        target_service = (
            MyAnimeListAnimeService(mal_client, reverse=False)
            if kind == "anime"
            else MyAnimeListMangaService(mal_client, reverse=False)
        )
        existing = {
            t.get_target_id(): t
            for t in (
                model.from_mal_entry(e, reverse=False)
                for e in (mal_client.get_user_anime_list() if kind == "anime" else mal_client.get_user_manga_list())
            )
        }

    chain = build_strategy_chain(
        kind,
        reverse=reverse,
        mappings=mappings,
        offline_database=offline_database,
        hato_client=hato_client,
        arm_client=arm_client,
        jikan_client=jikan_client,
        target_service=target_service,
    )
    updater = Updater(chain, target_service, mappings=mappings, force=force, dry_run=dry_run)
    outcome = updater.run(sources, existing, on_progress=on_progress)
    return outcome, anilist_entries, target_service


def _persist_unmapped(config: Config, kind: str, reverse: bool, outcome: SyncOutcome) -> None:
    direction = "reverse" if reverse else "forward"
    records = [
        UnmappedRecord.from_pipeline_entry(entry, media_type=kind, direction=direction)
        for entry in outcome.unmatched
    ]
    state = load_unmapped_state(config.resolved_unmapped_state_path)
    state.replace_run(kind, direction, records)
    save_unmapped_state(state, config.resolved_unmapped_state_path)


def _persist_sync_history(config: Config, outcomes: dict[str, SyncOutcome]) -> None:
    entry = SyncHistoryEntry.from_statistics(SyncStatistics.from_outcomes(outcomes))
    save_sync_history(entry, config.resolved_sync_history_path)


def _sync_favorites(
    config: Config,
    media_state: dict[str, tuple[SyncOutcome, list[Any]]],
    *,
    reverse: bool,
    jikan_client: JikanClient | None,
    anilist_client: AniListClient,
) -> dict[str, FavoritesOutcome]:
    """Returns the AniList->MAL report-only outcome per media kind, for the
    caller's report step to fold favorites mismatches into."""
    if jikan_client is None:
        logger.warning(
            "favorites sync requires the Jikan API; skipping "
            "(enable with --jikan-api or favorites.enabled in config)"
        )
        return {}

    try:
        mal_anime_fav_ids, mal_manga_fav_ids = jikan_client.get_user_favorites(config.myanimelist.username)
    except JikanApiError as exc:
        logger.warning("failed to fetch MAL favorites: %s", exc)
        return {}

    report_outcomes: dict[str, FavoritesOutcome] = {}
    for kind, (outcome, anilist_entries) in media_state.items():
        mal_fav_ids = mal_anime_fav_ids if kind == "anime" else mal_manga_fav_ids
        id_map = build_id_mapping(outcome.matched)
        if reverse:
            mal_to_anilist, anilist_to_mal = id_map, _invert(id_map)
        else:
            anilist_to_mal, mal_to_anilist = id_map, _invert(id_map)

        anilist_by_id = {e.id_anilist: e for e in anilist_entries}
        write_outcome = sync_mal_favorites_to_anilist(
            mal_fav_ids, anilist_by_id, mal_to_anilist, anilist_client, media_kind=kind
        )
        logger.info("%s favorites: added %d to AniList", kind, len(write_outcome.added_to_anilist))
        report_outcome = check_anilist_favorites_against_mal(anilist_entries, mal_fav_ids, anilist_to_mal)

        # build_report only takes one FavoritesOutcome per kind; fold the
        # write-side errors into the report-side mismatches so both surface
        # in the final summary.
        report_outcome.errors = write_outcome.errors
        report_outcomes[kind] = report_outcome

    return report_outcomes
