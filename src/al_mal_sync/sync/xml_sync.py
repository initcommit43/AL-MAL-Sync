"""Import/export of MAL-format list XML files, layered on top of the exact
same building blocks runner.py wires together for live sync -- Anime/Manga's
from_*_entry constructors, build_strategy_chain, Updater, and the four
MediaService adapters. runner.py itself is untouched: a file is just another
Source, so importing one only needs a different `sources` list plugged into
the same resolve -> deduplicate -> process pipeline live sync already uses.

Export needs none of that matching machinery -- it's a live fetch (source,
not target) immediately serialized, so it only pulls in the client/oauth
setup.
"""

from __future__ import annotations

import logging

from .. import xml_list
from ..clients.anilist import AniListAPIError, AniListClient
from ..clients.myanimelist import MyAnimeListClient
from ..config import Config, parse_duration
from ..mapping.arm_api import ArmApiClient
from ..mapping.hato_api import HatoApiClient
from ..mapping.jikan_api import JikanClient
from ..mapping.manual_mappings import load_mappings
from ..mapping.offline_database import OfflineDatabase, OfflineDatabaseError, load_offline_database
from ..models import Anime, Manga
from ..oauth import create_anilist_oauth, create_myanimelist_oauth
from ..unmapped import UnmappedRecord, load_unmapped_state, save_unmapped_state
from .runner import OnProgress, build_strategy_chain
from .service import (
    AniListAnimeService,
    AniListMangaService,
    MyAnimeListAnimeService,
    MyAnimeListMangaService,
)
from .updater import SyncOutcome, Updater

logger = logging.getLogger(__name__)

_SERVICES = ("anilist", "myanimelist")


class XmlSyncError(Exception):
    """Raised for invalid import/export arguments (bad service name, etc.)."""


def _validate_service(service: str) -> None:
    if service not in _SERVICES:
        raise XmlSyncError(f"unknown service {service!r}, expected one of {_SERVICES}")


def _build_clients(config: Config) -> tuple[AniListClient, MyAnimeListClient, float]:
    anilist_oauth = create_anilist_oauth(config)
    mal_oauth = create_myanimelist_oauth(config)
    http_timeout = config.get_http_timeout().total_seconds()
    anilist_client = AniListClient(anilist_oauth, config.anilist.username, http_timeout=http_timeout)
    mal_client = MyAnimeListClient(mal_oauth, config.myanimelist.username, http_timeout=http_timeout)
    return anilist_client, mal_client, http_timeout


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def run_export(
    config: Config,
    *,
    service: str,
    manga: bool = False,
    all_media: bool = False,
) -> dict[str, str]:
    """Fetch `service`'s live list and serialize it to MAL-format XML.

    Returns a dict of media kind ("anime"/"manga") -> XML document string,
    one entry per requested kind. No id-mapping/matching involved -- export
    only ever reads the source service's own data as-is.
    """
    _validate_service(service)
    media_kinds = ["anime", "manga"] if all_media else (["manga"] if manga else ["anime"])
    anilist_client, mal_client, _timeout = _build_clients(config)

    username = config.anilist.username if service == "anilist" else config.myanimelist.username

    documents: dict[str, str] = {}
    for kind in media_kinds:
        model = Anime if kind == "anime" else Manga
        if service == "anilist":
            try:
                score_format = anilist_client.get_user_score_format()
            except AniListAPIError as exc:
                raise AniListAPIError(
                    f"failed to fetch AniList score format: {exc}",
                    status_code=getattr(exc, "status_code", None),
                ) from exc
            entries = [
                model.from_anilist_entry(e, score_format, reverse=False)
                for e in (
                    anilist_client.get_user_anime_list()
                    if kind == "anime"
                    else anilist_client.get_user_manga_list()
                )
            ]
        else:
            entries = [
                model.from_mal_entry(e, reverse=False)
                for e in (
                    mal_client.get_user_anime_list() if kind == "anime" else mal_client.get_user_manga_list()
                )
            ]

        documents[kind] = (
            xml_list.anime_list_to_xml(entries, username=username)
            if kind == "anime"
            else xml_list.manga_list_to_xml(entries, username=username)
        )

    return documents


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def run_import(
    config: Config,
    *,
    xml_text: str,
    target_service: str,
    kind: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    offline_db: bool = False,
    offline_db_force_refresh: bool = False,
    arm_api: bool = False,
    arm_api_url: str | None = None,
    jikan_api: bool = False,
    on_progress: OnProgress | None = None,
) -> tuple[str, SyncOutcome]:
    """Push a MAL-format XML file's entries into `target_service`.

    Returns the resolved media kind alongside the outcome so a caller that
    passed `kind=None` still knows what was actually imported.

    `kind` ("anime"/"manga") is auto-detected from the file when omitted.
    Importing into AniList reuses the exact matching pipeline (and
    strategies) that live MyAnimeList -> AniList sync already uses, since an
    XML entry and a live MAL entry are both just "a MAL-shaped Source with no
    known AniList id yet". Importing into MyAnimeList is a direct id_mal
    lookup against the user's existing list, same as forward sync.
    """
    _validate_service(target_service)
    resolved_kind = kind or xml_list.detect_kind(xml_text)
    if resolved_kind not in ("anime", "manga"):
        raise XmlSyncError(f"unknown media kind {resolved_kind!r}")

    xml_entries = (
        xml_list.parse_anime_xml(xml_text)
        if resolved_kind == "anime"
        else xml_list.parse_manga_xml(xml_text)
    )

    mappings = load_mappings(config.resolved_mappings_file_path)
    anilist_client, mal_client, http_timeout = _build_clients(config)
    model = Anime if resolved_kind == "anime" else Manga
    reverse = target_service == "anilist"

    if reverse:
        score_format = anilist_client.get_user_score_format()
        sources = [model.from_mal_entry(e, reverse=True) for e in xml_entries]
        target = (
            AniListAnimeService(anilist_client, score_format, reverse=True)
            if resolved_kind == "anime"
            else AniListMangaService(anilist_client, score_format, reverse=True)
        )
        existing = {
            t.get_target_id(): t
            for t in (
                model.from_anilist_entry(e, score_format, reverse=True)
                for e in (
                    anilist_client.get_user_anime_list()
                    if resolved_kind == "anime"
                    else anilist_client.get_user_manga_list()
                )
            )
        }
    else:
        sources = [model.from_mal_entry(e, reverse=False) for e in xml_entries]
        target = (
            MyAnimeListAnimeService(mal_client, reverse=False)
            if resolved_kind == "anime"
            else MyAnimeListMangaService(mal_client, reverse=False)
        )
        existing = {
            t.get_target_id(): t
            for t in (
                model.from_mal_entry(e, reverse=False)
                for e in (
                    mal_client.get_user_anime_list()
                    if resolved_kind == "anime"
                    else mal_client.get_user_manga_list()
                )
            )
        }

    offline_database: OfflineDatabase | None = None
    if resolved_kind == "anime" and (config.offline_database.enabled or offline_db):
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
    if resolved_kind == "anime" and (config.arm_api.enabled or arm_api):
        arm_client = ArmApiClient(arm_api_url or config.arm_api.base_url, http_timeout=http_timeout)

    jikan_client: JikanClient | None = None
    if config.jikan_api.enabled or jikan_api:
        jikan_client = JikanClient(
            config.resolved_jikan_cache_dir,
            cache_max_age_seconds=parse_duration(config.jikan_api.cache_max_age).total_seconds(),
            http_timeout=http_timeout,
        )

    try:
        chain = build_strategy_chain(
            resolved_kind,
            reverse=reverse,
            mappings=mappings,
            offline_database=offline_database,
            hato_client=hato_client,
            arm_client=arm_client,
            jikan_client=jikan_client,
            target_service=target,
        )
        updater = Updater(chain, target, mappings=mappings, force=force, dry_run=dry_run)
        outcome = updater.run(sources, existing, on_progress=on_progress)
    finally:
        if hato_client is not None:
            hato_client.save_cache()
        if jikan_client is not None:
            jikan_client.save_cache()

    _persist_unmapped(config, resolved_kind, reverse, outcome)
    return resolved_kind, outcome


def _persist_unmapped(config: Config, kind: str, reverse: bool, outcome: SyncOutcome) -> None:
    direction = "reverse" if reverse else "forward"
    records = [
        UnmappedRecord.from_pipeline_entry(entry, media_type=kind, direction=direction)
        for entry in outcome.unmatched
    ]
    state = load_unmapped_state(config.resolved_unmapped_state_path)
    state.replace_run(kind, direction, records)
    save_unmapped_state(state, config.resolved_unmapped_state_path)
