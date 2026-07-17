"""CLI commands: login, logout, status, sync, watch, unmapped.

Ported from the reference Go tool's cmd_*.go files (one Click command per Go
command, same flag names for muscle-memory parity). This module also owns the
sync orchestration glue: wiring the OAuth-backed API clients, the id-mapping
strategy chain, and sync/updater.py's Updater together for a given
(media kind, direction) pair -- none of the lower-level modules know about
each other, so something has to assemble them, and the CLI is the natural
place since it already owns the run's flags/config.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

import click
from croniter import CroniterError, croniter

from .clients.anilist import AniListAPIError, AniListClient
from .clients.myanimelist import MyAnimeListAPIError, MyAnimeListClient
from .config import Config, ConfigError, load_config, parse_duration
from .logging_config import configure_logging
from .mapping.arm_api import ArmApiClient
from .mapping.hato_api import HatoApiClient
from .mapping.jikan_api import JikanApiError, JikanClient
from .mapping.manual_mappings import MappingsConfig, load_mappings
from .mapping.offline_database import OfflineDatabase, OfflineDatabaseError, load_offline_database
from .mapping.strategies import (
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
from .models import Anime, Manga
from .oauth import OAuth, OAuthError, create_anilist_oauth, create_myanimelist_oauth
from .sync.favorites import (
    FavoritesOutcome,
    build_id_mapping,
    check_anilist_favorites_against_mal,
    sync_mal_favorites_to_anilist,
)
from .sync.report import build_report, format_report
from .sync.service import (
    AniListAnimeService,
    AniListMangaService,
    MyAnimeListAnimeService,
    MyAnimeListMangaService,
)
from .sync.statistics import SyncStatistics, format_statistics_table
from .sync.updater import SyncOutcome, Updater
from .unmapped import UnmappedRecord, load_unmapped_state, save_unmapped_state

_SERVICE_CHOICES = ("anilist", "myanimelist", "all")


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _load_config(ctx: click.Context) -> Config:
    path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        return load_config(path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _selected_services(service: str) -> list[str]:
    return ["anilist", "myanimelist"] if service == "all" else [service]


def _oauth_for(service: str, config: Config) -> OAuth:
    return create_anilist_oauth(config) if service == "anilist" else create_myanimelist_oauth(config)


def _format_expiry(expiry: float | None) -> str:
    if expiry is None:
        return "never"
    return datetime.fromtimestamp(expiry).isoformat(timespec="seconds")


def _sync_options(f: Any) -> Any:
    """Shared flag set for `sync` and `watch`, applied as a stacked decorator
    so both commands parse identical options without repeating them."""
    options = [
        click.option("--force", "-f", is_flag=True, help="Skip strategy matching; sync every source directly by ID."),
        click.option("--dry-run", "-d", is_flag=True, help="Don't write updates, just report what would change."),
        click.option("--manga", is_flag=True, help="Sync manga instead of anime."),
        click.option("--all", "all_media", is_flag=True, help="Sync both anime and manga."),
        click.option("--reverse-direction", is_flag=True, help="Sync MyAnimeList -> AniList instead of AniList -> MyAnimeList."),
        click.option("--offline-db", is_flag=True, help="Force-enable the offline anime database id-mapping strategy."),
        click.option("--offline-db-force-refresh", is_flag=True, help="Force re-download the offline anime database cache."),
        click.option("--arm-api", is_flag=True, help="Enable the ARM API id-mapping fallback (anime only)."),
        click.option("--arm-api-url", default=None, help="Override the ARM API base URL."),
        click.option("--jikan-api", is_flag=True, help="Enable the Jikan API id-mapping fallback (manga only)."),
        click.option("--favorites", is_flag=True, help="Also sync favorites (implies --jikan-api)."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


# --------------------------------------------------------------------------
# Group
# --------------------------------------------------------------------------


@click.group()
@click.option(
    "--config", "-c", "config_path",
    type=click.Path(dir_okay=False), default=None,
    help="Path to config.yaml (defaults to environment-variable-only configuration).",
)
@click.pass_context
def main(ctx: click.Context, config_path: str | None) -> None:
    """al-mal-sync: bidirectional sync between AniList and MyAnimeList."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


# --------------------------------------------------------------------------
# login / logout / status
# --------------------------------------------------------------------------


@main.command()
@click.option("--service", "-s", type=click.Choice(_SERVICE_CHOICES), default="all")
@click.pass_context
def login(ctx: click.Context, service: str) -> None:
    """Authenticate with AniList and/or MyAnimeList."""
    config = _load_config(ctx)
    for name in _selected_services(service):
        oauth = _oauth_for(name, config)
        if not oauth.needs_init:
            click.echo(f"{name}: already authenticated")
            continue
        try:
            oauth.login(config.oauth.port)
        except OAuthError as exc:
            raise click.ClickException(f"{name}: {exc}") from exc
        click.secho(f"{name}: login successful", fg="green")


@main.command()
@click.option("--service", "-s", type=click.Choice(_SERVICE_CHOICES), default="all")
@click.pass_context
def logout(ctx: click.Context, service: str) -> None:
    """Remove stored credentials for AniList and/or MyAnimeList."""
    config = _load_config(ctx)
    for name in _selected_services(service):
        _oauth_for(name, config).delete_token()
        click.echo(f"{name}: logged out")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show authentication status for both services."""
    config = _load_config(ctx)
    for name in ("anilist", "myanimelist"):
        oauth = _oauth_for(name, config)
        if oauth.needs_init:
            click.echo(f"{name}: not authenticated")
        elif oauth.is_token_valid:
            click.secho(f"{name}: authenticated (expires {_format_expiry(oauth.token_expiry)})", fg="green")
        else:
            click.secho(f"{name}: token expired, will refresh on next use", fg="yellow")


# --------------------------------------------------------------------------
# sync orchestration
# --------------------------------------------------------------------------


def _build_strategy_chain(
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


def _announce_kind(kind: str, reverse: bool) -> None:
    direction = "MyAnimeList -> AniList" if reverse else "AniList -> MyAnimeList"
    click.echo(f"Syncing {kind} ({direction})...")


def _print_summary(outcomes: dict[str, SyncOutcome], favorites_outcomes: dict[str, FavoritesOutcome]) -> None:
    click.echo("\n" + format_statistics_table(SyncStatistics.from_outcomes(outcomes)))
    click.echo("\n" + format_report(build_report(outcomes, favorites_outcomes)))


def _invert(mapping: dict[int, int]) -> dict[int, int]:
    return {v: k for k, v in mapping.items()}


def _run_sync(
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
) -> None:
    media_kinds = ["anime", "manga"] if all_media else (["manga"] if manga else ["anime"])
    if favorites:
        jikan_api = True

    mappings = load_mappings(config.resolved_mappings_file_path)

    try:
        anilist_oauth = create_anilist_oauth(config)
        mal_oauth = create_myanimelist_oauth(config)
    except OAuthError as exc:
        raise click.ClickException(str(exc)) from exc

    http_timeout = config.get_http_timeout().total_seconds()
    anilist_client = AniListClient(anilist_oauth, config.anilist.username, http_timeout=http_timeout)
    mal_client = MyAnimeListClient(mal_oauth, config.myanimelist.username, http_timeout=http_timeout)

    try:
        score_format = anilist_client.get_user_score_format()
    except AniListAPIError as exc:
        raise click.ClickException(f"failed to fetch AniList score format: {exc}") from exc

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
            click.secho(f"warning: offline database unavailable: {exc}", fg="yellow", err=True)

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
            _announce_kind(kind, reverse)
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
            )
            media_state[kind] = (outcome, anilist_entries)
            _persist_unmapped(config, kind, reverse, outcome)

        favorites_outcomes: dict[str, FavoritesOutcome] = {}
        if favorites:
            favorites_outcomes = _sync_favorites(
                config, media_state, reverse=reverse, jikan_client=jikan_client, anilist_client=anilist_client
            )

        outcomes = {kind: outcome for kind, (outcome, _entries) in media_state.items()}
        _print_summary(outcomes, favorites_outcomes)
    except (AniListAPIError, MyAnimeListAPIError) as exc:
        raise click.ClickException(str(exc)) from exc
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

    chain = _build_strategy_chain(
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
    outcome = updater.run(sources, existing)
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


def _sync_favorites(
    config: Config,
    media_state: dict[str, tuple[SyncOutcome, list[Any]]],
    *,
    reverse: bool,
    jikan_client: JikanClient | None,
    anilist_client: AniListClient,
) -> dict[str, FavoritesOutcome]:
    """Returns the AniList->MAL report-only outcome per media kind, for
    _print_summary's report section to fold favorites mismatches into."""
    if jikan_client is None:
        click.secho(
            "warning: favorites sync requires the Jikan API; skipping "
            "(enable with --jikan-api or favorites.enabled in config)",
            fg="yellow", err=True,
        )
        return {}

    try:
        mal_anime_fav_ids, mal_manga_fav_ids = jikan_client.get_user_favorites(config.myanimelist.username)
    except JikanApiError as exc:
        click.secho(f"warning: failed to fetch MAL favorites: {exc}", fg="yellow", err=True)
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
        click.echo(f"{kind} favorites: added {len(write_outcome.added_to_anilist)} to AniList")
        report_outcome = check_anilist_favorites_against_mal(anilist_entries, mal_fav_ids, anilist_to_mal)

        # build_report only takes one FavoritesOutcome per kind; fold the
        # write-side errors into the report-side mismatches so both surface
        # in the final summary.
        report_outcome.errors = write_outcome.errors
        report_outcomes[kind] = report_outcome

    return report_outcomes


@main.command()
@_sync_options
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def sync(
    ctx: click.Context,
    force: bool, dry_run: bool, manga: bool, all_media: bool, reverse_direction: bool,
    offline_db: bool, offline_db_force_refresh: bool, arm_api: bool, arm_api_url: str | None,
    jikan_api: bool, favorites: bool, verbose: bool,
) -> None:
    """Sync anime/manga lists between AniList and MyAnimeList."""
    configure_logging(verbose)
    config = _load_config(ctx)
    _run_sync(
        config,
        force=force, dry_run=dry_run, manga=manga, all_media=all_media, reverse=reverse_direction,
        offline_db=offline_db, offline_db_force_refresh=offline_db_force_refresh,
        arm_api=arm_api, arm_api_url=arm_api_url, jikan_api=jikan_api, favorites=favorites,
    )


# --------------------------------------------------------------------------
# watch
# --------------------------------------------------------------------------


@main.command()
@click.option("--interval", "-i", default=None, help="Sync interval (e.g. '6h'); overrides config/env watch.interval.")
@click.option("--schedule", "-s", default=None, help="Cron schedule (5 fields); overrides config/env watch.schedule.")
@click.option("--once", is_flag=True, help="Run a single sync immediately and exit, ignoring interval/schedule.")
@_sync_options
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def watch(
    ctx: click.Context,
    interval: str | None, schedule: str | None, once: bool,
    force: bool, dry_run: bool, manga: bool, all_media: bool, reverse_direction: bool,
    offline_db: bool, offline_db_force_refresh: bool, arm_api: bool, arm_api_url: str | None,
    jikan_api: bool, favorites: bool, verbose: bool,
) -> None:
    """Run sync repeatedly on an interval or cron schedule."""
    configure_logging(verbose)
    config = _load_config(ctx)

    if interval:
        config.watch.interval, config.watch.schedule = interval, ""
    if schedule:
        config.watch.schedule, config.watch.interval = schedule, ""

    def run_once() -> None:
        _run_sync(
            config,
            force=force, dry_run=dry_run, manga=manga, all_media=all_media, reverse=reverse_direction,
            offline_db=offline_db, offline_db_force_refresh=offline_db_force_refresh,
            arm_api=arm_api, arm_api_url=arm_api_url, jikan_api=jikan_api, favorites=favorites,
        )

    if once:
        run_once()
        return

    config.watch.validate()

    if config.watch.schedule:
        _run_cron_loop(config.watch.schedule, run_once)
        return

    interval_delta = config.watch.get_interval()
    assert interval_delta is not None
    click.echo(f"watch mode: syncing every {interval_delta}")
    while True:
        run_once()
        click.echo(f"next sync in {interval_delta}")
        time.sleep(interval_delta.total_seconds())


def _run_cron_loop(schedule: str, run_once: Callable[[], None]) -> None:
    """Sync on a cron schedule, evaluated in local time (so the system's TZ,
    not UTC, decides when "0 3 * * *" fires) -- matches PLAN.md Phase 10.
    Phase 1's WatchConfig.validate() only checks the expression has 5
    fields; croniter itself validates the field contents here."""
    try:
        cron = croniter(schedule, datetime.now())
    except CroniterError as exc:
        raise click.ClickException(f"invalid watch schedule {schedule!r}: {exc}") from exc

    click.echo(f"watch mode: syncing on schedule {schedule!r}")
    while True:
        next_run: datetime = cron.get_next(datetime)
        wait_seconds = (next_run - datetime.now()).total_seconds()
        if wait_seconds > 0:
            click.echo(f"next sync at {next_run.isoformat(timespec='seconds')} (in {wait_seconds:.0f}s)")
            time.sleep(wait_seconds)
        run_once()


# --------------------------------------------------------------------------
# unmapped
# --------------------------------------------------------------------------


@main.command()
@click.option("--fix", is_flag=True, help="Interactively resolve unmapped entries.")
@click.option("--ignore-all", is_flag=True, help="Add every unmapped entry to the ignore list without prompting.")
@click.pass_context
def unmapped(ctx: click.Context, fix: bool, ignore_all: bool) -> None:
    """View or resolve entries the last sync couldn't map."""
    config = _load_config(ctx)
    state = load_unmapped_state(config.resolved_unmapped_state_path)

    if not state.entries:
        click.echo("No unmapped entries.")
        return

    if ignore_all:
        mappings = load_mappings(config.resolved_mappings_file_path)
        for entry in state.entries:
            if entry.anilist_id > 0:
                mappings.add_ignore_by_id(entry.anilist_id)
            elif entry.mal_id > 0:
                mappings.add_ignore_by_mal_id(entry.mal_id)
        mappings.save(config.resolved_mappings_file_path)
        state.clear()
        save_unmapped_state(state, config.resolved_unmapped_state_path)
        click.echo("Ignored all unmapped entries.")
        return

    if not fix:
        for entry in state.entries:
            click.echo(
                f"[{entry.media_type}/{entry.direction}] {entry.title} "
                f"(anilist={entry.anilist_id or '-'}, mal={entry.mal_id or '-'}): {entry.reason}"
            )
        return

    mappings = load_mappings(config.resolved_mappings_file_path)
    for entry in list(state.entries):
        click.echo(f"\n[{entry.media_type}/{entry.direction}] {entry.title}: {entry.reason}")
        choice = click.prompt(
            "  (i)gnore by id, (t)itle, (m)anual mapping, (s)kip",
            type=click.Choice(["i", "t", "m", "s"]), default="s",
        )
        if choice == "i":
            if entry.anilist_id > 0:
                mappings.add_ignore_by_id(entry.anilist_id)
            elif entry.mal_id > 0:
                mappings.add_ignore_by_mal_id(entry.mal_id)
            if entry in state.entries:
                state.entries.remove(entry)
        elif choice == "t":
            mappings.ignore.titles.append(entry.title)
            state.remove_by_title(entry.title)
        elif choice == "m":
            mal_id = click.prompt("  MAL id", type=int)
            anilist_id = entry.anilist_id or click.prompt("  AniList id", type=int)
            mappings.add_manual_mapping(anilist_id, mal_id)
            if entry in state.entries:
                state.entries.remove(entry)

    mappings.save(config.resolved_mappings_file_path)
    save_unmapped_state(state, config.resolved_unmapped_state_path)
    click.echo("\nDone.")
