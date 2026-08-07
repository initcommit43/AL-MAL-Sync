"""CLI commands: login, logout, status, sync, watch, unmapped.

Ported from the reference Go tool's cmd_*.go files (one Click command per Go
command, same flag names for muscle-memory parity). Sync orchestration itself
lives in sync/runner.py (shared with any other frontend); this module wires
click's flags/config into that, prints results, and turns typed exceptions
into ClickExceptions.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import click
from croniter import CroniterError, croniter

from . import xml_list
from .clients.anilist import AniListAPIError
from .clients.myanimelist import MyAnimeListAPIError
from .config import Config, ConfigError, load_config
from .logging_config import configure_logging
from .mapping.manual_mappings import load_mappings
from .oauth import OAuth, OAuthError, create_anilist_oauth, create_myanimelist_oauth
from .sync.favorites import FavoritesOutcome
from .sync.report import build_report, format_report
from .sync.runner import run_sync
from .sync.statistics import SyncStatistics, format_statistics_table
from .sync.updater import SyncOutcome
from .sync.xml_sync import XmlSyncError, run_export, run_import
from .unmapped import load_unmapped_state, save_unmapped_state

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
    # Titles routinely contain Japanese/CJK characters; a non-UTF-8 console
    # (e.g. Windows cp1252) would otherwise crash on the final report print.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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


def _announce_kind(kind: str, reverse: bool) -> None:
    direction = "MyAnimeList -> AniList" if reverse else "AniList -> MyAnimeList"
    click.echo(f"Syncing {kind} ({direction})...")


def _print_summary(outcomes: dict[str, SyncOutcome], favorites_outcomes: dict[str, FavoritesOutcome]) -> None:
    click.echo("\n" + format_statistics_table(SyncStatistics.from_outcomes(outcomes)))
    click.echo("\n" + format_report(build_report(outcomes, favorites_outcomes)))


def _run_sync_command(
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
    """CLI wrapper around sync.runner.run_sync(): announces each kind and
    prints the final summary via click, and turns the typed exceptions
    run_sync() raises into a clean ClickException."""
    try:
        outcomes, favorites_outcomes = run_sync(
            config,
            force=force, dry_run=dry_run, manga=manga, all_media=all_media, reverse=reverse,
            offline_db=offline_db, offline_db_force_refresh=offline_db_force_refresh,
            arm_api=arm_api, arm_api_url=arm_api_url, jikan_api=jikan_api, favorites=favorites,
            on_kind_start=_announce_kind,
        )
    except (OAuthError, AniListAPIError, MyAnimeListAPIError) as exc:
        raise click.ClickException(str(exc)) from exc

    _print_summary(outcomes, favorites_outcomes)


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
    _run_sync_command(
        config,
        force=force, dry_run=dry_run, manga=manga, all_media=all_media, reverse=reverse_direction,
        offline_db=offline_db, offline_db_force_refresh=offline_db_force_refresh,
        arm_api=arm_api, arm_api_url=arm_api_url, jikan_api=jikan_api, favorites=favorites,
    )


# --------------------------------------------------------------------------
# export / import (MAL-format XML list files)
# --------------------------------------------------------------------------


def _default_export_filename(service: str, kind: str) -> str:
    return f"{service}_{kind}.xml"


@main.command()
@click.option("--service", "-s", type=click.Choice(("anilist", "myanimelist")), required=True, help="Which service's list to export.")
@click.option("--manga", is_flag=True, help="Export manga instead of anime.")
@click.option("--all", "all_media", is_flag=True, help="Export both anime and manga.")
@click.option("--output", "-o", "output_path", type=click.Path(dir_okay=False), default=None, help="Output file path. Not usable with --all (writes one file per kind instead).")
@click.option("--output-dir", type=click.Path(file_okay=False), default=".", help="Directory to write into when --output isn't given.")
@click.pass_context
def export(
    ctx: click.Context,
    service: str, manga: bool, all_media: bool, output_path: str | None, output_dir: str,
) -> None:
    """Export an AniList or MyAnimeList list to a MAL-format XML file."""
    if output_path and all_media:
        raise click.ClickException("--output can't be combined with --all; use --output-dir instead")

    config = _load_config(ctx)
    try:
        documents = run_export(config, service=service, manga=manga, all_media=all_media)
    except (OAuthError, AniListAPIError, MyAnimeListAPIError, XmlSyncError) as exc:
        raise click.ClickException(str(exc)) from exc

    for kind, xml_text in documents.items():
        path = Path(output_path) if output_path else Path(output_dir) / _default_export_filename(service, kind)
        path.write_text(xml_text, encoding="utf-8")
        click.secho(f"Wrote {kind} list ({service}) -> {path}", fg="green")


@main.command(name="import")
@click.option("--file", "-i", "file_path", type=click.Path(exists=True, dir_okay=False), required=True, help="MAL-format XML file to import.")
@click.option("--target", "-t", "target_service", type=click.Choice(("anilist", "myanimelist")), required=True, help="Which service to import the list into.")
@click.option("--manga", is_flag=True, help="Treat the file as a manga list instead of auto-detecting.")
@click.option("--force", "-f", is_flag=True, help="Skip strategy matching; import every entry directly by ID.")
@click.option("--dry-run", "-d", is_flag=True, help="Don't write updates, just report what would change.")
@click.option("--offline-db", is_flag=True, help="Force-enable the offline anime database id-mapping strategy.")
@click.option("--offline-db-force-refresh", is_flag=True, help="Force re-download the offline anime database cache.")
@click.option("--arm-api", is_flag=True, help="Enable the ARM API id-mapping fallback (anime only).")
@click.option("--arm-api-url", default=None, help="Override the ARM API base URL.")
@click.option("--jikan-api", is_flag=True, help="Enable the Jikan API id-mapping fallback (manga only).")
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
@click.pass_context
def import_(
    ctx: click.Context,
    file_path: str, target_service: str, manga: bool, force: bool, dry_run: bool,
    offline_db: bool, offline_db_force_refresh: bool, arm_api: bool, arm_api_url: str | None,
    jikan_api: bool, verbose: bool,
) -> None:
    """Import a MAL-format XML list file into AniList or MyAnimeList."""
    configure_logging(verbose)
    config = _load_config(ctx)
    xml_text = Path(file_path).read_text(encoding="utf-8")

    click.echo(f"Importing {file_path} -> {target_service}...")
    try:
        kind, outcome = run_import(
            config,
            xml_text=xml_text,
            target_service=target_service,
            kind="manga" if manga else None,
            force=force, dry_run=dry_run,
            offline_db=offline_db, offline_db_force_refresh=offline_db_force_refresh,
            arm_api=arm_api, arm_api_url=arm_api_url, jikan_api=jikan_api,
        )
    except (OAuthError, AniListAPIError, MyAnimeListAPIError, XmlSyncError, xml_list.XmlListError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("\n" + format_statistics_table(SyncStatistics.from_outcomes({kind: outcome})))


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
        _run_sync_command(
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
