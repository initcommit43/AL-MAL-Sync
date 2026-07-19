"""Load config from config.yaml / env vars (env overrides file).

Priority: environment variable > config.yaml > built-in default.

Ported from the reference Go tool's config.go. One deliberate deviation: cache/state
directory defaults (offline-db cache, Hato/Jikan cache, unmapped state) are resolved
lazily via ``Config.resolved_*`` properties instead of being eagerly baked into the
dataclass at load time. This is simpler and avoids computing filesystem paths for
features that may not even be enabled.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

APP_NAME = "al-mal-sync"

DEFAULT_ANILIST_AUTH_URL = "https://anilist.co/api/v2/oauth/authorize"
DEFAULT_ANILIST_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"
DEFAULT_MAL_AUTH_URL = "https://myanimelist.net/v1/oauth2/authorize"
DEFAULT_MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
DEFAULT_ARM_BASE_URL = "https://arm.haglund.dev"
DEFAULT_HATO_BASE_URL = "https://hato.malupdaterosx.moe"
DEFAULT_HTTP_TIMEOUT = "30s"

MIN_WATCH_INTERVAL = timedelta(hours=1)
MAX_WATCH_INTERVAL = timedelta(hours=168)  # 7 days

_DURATION_TOKEN_RE = re.compile(r"(\d+)(h|m|s)")
_DURATION_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


# --------------------------------------------------------------------------
# Duration / cron helpers
# --------------------------------------------------------------------------


def parse_duration(value: str) -> timedelta:
    """Parse a Go-style duration string such as "12h", "90m", or "1h30m"."""
    if not value:
        raise ConfigError("empty duration string")

    # finditer() happily skips over characters that don't match, so on its own it
    # would accept garbage like "12hxyz" or "abc12h" as long as a valid token shows
    # up somewhere. Tracking `pos` and requiring each match to start exactly where
    # the last one ended is what catches that: any gap or leftover text fails the
    # pos check below.
    total_seconds = 0
    pos = 0
    for match in _DURATION_TOKEN_RE.finditer(value):
        if match.start() != pos:
            raise ConfigError(f"invalid duration {value!r}")
        amount, unit = match.groups()
        total_seconds += int(amount) * _DURATION_UNIT_SECONDS[unit]
        pos = match.end()

    if pos != len(value) or total_seconds == 0:
        raise ConfigError(f"invalid duration {value!r}")

    return timedelta(seconds=total_seconds)


def _validate_cron_shape(expr: str) -> None:
    """Sanity-check a cron expression has 5 whitespace-separated fields.

    Full semantic validation (valid ranges, step values, etc.) is deferred to
    Phase 10 when a real cron parser (``croniter``) is wired into watch mode.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ConfigError(
            f"invalid watch schedule {expr!r}: expected 5 fields "
            f"(minute hour dom month dow), got {len(fields)}"
        )


# --------------------------------------------------------------------------
# Default path helpers
# --------------------------------------------------------------------------


def user_config_dir() -> Path:
    """Approximate Go's os.UserConfigDir() without adding a dependency."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) if base else Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


def app_config_dir() -> Path:
    return user_config_dir() / APP_NAME


def default_config_path() -> str:
    """Where the GUI reads/writes config.yaml by default. The CLI has no
    equivalent default -- it requires an explicit `-c`/`--config` path or
    env-only configuration -- this exists so the GUI's Settings screen has
    somewhere sensible to save to without the user picking a location."""
    return str(app_config_dir() / "config.yaml")


def default_token_path() -> str:
    return str(app_config_dir() / "token.json")


def default_mappings_path() -> str:
    return str(app_config_dir() / "mappings.yaml")


def default_offline_db_cache_dir() -> str:
    return str(app_config_dir() / "aod-cache")


def default_hato_cache_dir() -> str:
    return str(app_config_dir() / "hato-cache")


def default_jikan_cache_dir() -> str:
    return str(app_config_dir() / "jikan-cache")


def default_unmapped_state_path() -> str:
    return str(app_config_dir() / "state" / "unmapped.json")


# --------------------------------------------------------------------------
# Config schema
# --------------------------------------------------------------------------


@dataclass
class OAuthConfig:
    port: str = "18080"
    redirect_uri: str = "http://localhost:18080/callback"


@dataclass
class SiteConfig:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    auth_url: str = ""
    token_url: str = ""


@dataclass
class WatchConfig:
    interval: str = ""
    schedule: str = ""

    def get_interval(self) -> timedelta | None:
        if not self.interval:
            return None
        return parse_duration(self.interval)

    def validate(self) -> None:
        """Raise ConfigError unless exactly one of interval/schedule is set and valid."""
        if self.interval and self.schedule:
            raise ConfigError(
                "watch mode accepts either interval or schedule, not both "
                f"(interval={self.interval!r}, schedule={self.schedule!r})"
            )
        if self.interval:
            duration = parse_duration(self.interval)
            if duration < MIN_WATCH_INTERVAL:
                raise ConfigError(f"interval must be at least 1h (got {self.interval})")
            if duration > MAX_WATCH_INTERVAL:
                raise ConfigError(f"interval must be at most 168h/7days (got {self.interval})")
            return
        if self.schedule:
            _validate_cron_shape(self.schedule)
            return
        raise ConfigError("watch mode requires either interval or schedule")


@dataclass
class OfflineDatabaseConfig:
    enabled: bool = True
    cache_dir: str = ""
    auto_update: bool = True
    force_refresh: bool = False  # CLI flag only; never set from YAML/env


@dataclass
class ArmApiConfig:
    enabled: bool = False
    base_url: str = DEFAULT_ARM_BASE_URL


@dataclass
class HatoApiConfig:
    enabled: bool = True
    base_url: str = DEFAULT_HATO_BASE_URL
    cache_dir: str = ""
    cache_max_age: str = "720h"


@dataclass
class JikanApiConfig:
    enabled: bool = False
    cache_dir: str = ""
    cache_max_age: str = "168h"


@dataclass
class FavoritesConfig:
    enabled: bool = False


@dataclass
class Config:
    # anilist/myanimelist each need their own default auth_url/token_url, so a plain
    # class-level default won't work here (it'd point both fields at the exact same
    # SiteConfig instance). default_factory with a lambda gives each Config instance
    # its own SiteConfig, pre-filled with the right URLs for that service.
    oauth: OAuthConfig = field(default_factory=OAuthConfig)
    anilist: SiteConfig = field(
        default_factory=lambda: SiteConfig(
            auth_url=DEFAULT_ANILIST_AUTH_URL, token_url=DEFAULT_ANILIST_TOKEN_URL
        )
    )
    myanimelist: SiteConfig = field(
        default_factory=lambda: SiteConfig(
            auth_url=DEFAULT_MAL_AUTH_URL, token_url=DEFAULT_MAL_TOKEN_URL
        )
    )
    token_file_path: str = ""
    mappings_file_path: str = ""
    watch: WatchConfig = field(default_factory=WatchConfig)
    http_timeout: str = DEFAULT_HTTP_TIMEOUT
    offline_database: OfflineDatabaseConfig = field(default_factory=OfflineDatabaseConfig)
    arm_api: ArmApiConfig = field(default_factory=ArmApiConfig)
    hato_api: HatoApiConfig = field(default_factory=HatoApiConfig)
    jikan_api: JikanApiConfig = field(default_factory=JikanApiConfig)
    favorites: FavoritesConfig = field(default_factory=FavoritesConfig)

    @property
    def resolved_token_file_path(self) -> str:
        return self.token_file_path or default_token_path()

    @property
    def resolved_mappings_file_path(self) -> str:
        return self.mappings_file_path or default_mappings_path()

    @property
    def resolved_offline_db_cache_dir(self) -> str:
        return self.offline_database.cache_dir or default_offline_db_cache_dir()

    @property
    def resolved_hato_cache_dir(self) -> str:
        return self.hato_api.cache_dir or default_hato_cache_dir()

    @property
    def resolved_jikan_cache_dir(self) -> str:
        return self.jikan_api.cache_dir or default_jikan_cache_dir()

    @property
    def resolved_unmapped_state_path(self) -> str:
        return default_unmapped_state_path()

    def get_http_timeout(self) -> timedelta:
        if not self.http_timeout:
            return timedelta(seconds=30)
        try:
            return parse_duration(self.http_timeout)
        except ConfigError:
            logger.warning(
                "invalid http_timeout %r, using default 30s", self.http_timeout
            )
            return timedelta(seconds=30)


# --------------------------------------------------------------------------
# Env var overlay
# --------------------------------------------------------------------------


# Note: an env var set to an empty string is treated the same as unset here, so
# it falls through to `default` instead of overwriting a config value with "".
# That's intentional, it mirrors the Go tool's getEnvOrDefault and means you can't
# accidentally blank out a value from a config.yaml by having an empty env var
# lying around in your shell.
def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value else default


def _env_first(*keys: str, default: str = "") -> str:
    """Return the first non-empty env var among `keys`, in priority order.

    Used for env var aliases where more than one name is accepted for the same
    setting (e.g. OAUTH_PORT vs the older PORT, or CLIENT_SECRET_ANILIST which
    predates ANILIST_CLIENT_SECRET). The order of `keys` matters: earlier keys win.
    """
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if not value:
        return default
    return value.strip().lower() in ("true", "1", "yes")


def _apply_env_overrides(cfg: Config) -> Config:
    cfg.oauth.port = _env_first("OAUTH_PORT", "PORT", default=cfg.oauth.port)
    cfg.oauth.redirect_uri = _env("OAUTH_REDIRECT_URI", cfg.oauth.redirect_uri)

    cfg.anilist.client_id = _env("ANILIST_CLIENT_ID", cfg.anilist.client_id)
    cfg.anilist.client_secret = _env_first(
        "ANILIST_CLIENT_SECRET", "CLIENT_SECRET_ANILIST", default=cfg.anilist.client_secret
    )
    cfg.anilist.username = _env("ANILIST_USERNAME", cfg.anilist.username)

    cfg.myanimelist.client_id = _env("MAL_CLIENT_ID", cfg.myanimelist.client_id)
    cfg.myanimelist.client_secret = _env_first(
        "MAL_CLIENT_SECRET", "CLIENT_SECRET_MYANIMELIST", default=cfg.myanimelist.client_secret
    )
    cfg.myanimelist.username = _env("MAL_USERNAME", cfg.myanimelist.username)

    cfg.token_file_path = _env("TOKEN_FILE_PATH", cfg.token_file_path)
    cfg.mappings_file_path = _env("MAPPINGS_FILE_PATH", cfg.mappings_file_path)

    cfg.watch.interval = _env("WATCH_INTERVAL", cfg.watch.interval)
    cfg.watch.schedule = _env("WATCH_SCHEDULE", cfg.watch.schedule)

    cfg.http_timeout = _env("HTTP_TIMEOUT", cfg.http_timeout)

    cfg.offline_database.enabled = _env_bool(
        "OFFLINE_DATABASE_ENABLED", cfg.offline_database.enabled
    )
    cfg.offline_database.cache_dir = _env(
        "OFFLINE_DATABASE_CACHE_DIR", cfg.offline_database.cache_dir
    )
    cfg.offline_database.auto_update = _env_bool(
        "OFFLINE_DATABASE_AUTO_UPDATE", cfg.offline_database.auto_update
    )

    cfg.arm_api.enabled = _env_bool("ARM_API_ENABLED", cfg.arm_api.enabled)
    cfg.arm_api.base_url = _env("ARM_API_URL", cfg.arm_api.base_url)

    cfg.hato_api.enabled = _env_bool("HATO_API_ENABLED", cfg.hato_api.enabled)
    cfg.hato_api.base_url = _env("HATO_API_URL", cfg.hato_api.base_url)
    cfg.hato_api.cache_dir = _env("HATO_API_CACHE_DIR", cfg.hato_api.cache_dir)
    cfg.hato_api.cache_max_age = _env("HATO_API_CACHE_MAX_AGE", cfg.hato_api.cache_max_age)

    cfg.jikan_api.enabled = _env_bool("JIKAN_API_ENABLED", cfg.jikan_api.enabled)
    cfg.jikan_api.cache_dir = _env("JIKAN_API_CACHE_DIR", cfg.jikan_api.cache_dir)
    cfg.jikan_api.cache_max_age = _env("JIKAN_API_CACHE_MAX_AGE", cfg.jikan_api.cache_max_age)

    cfg.favorites.enabled = _env_bool("FAVORITES_SYNC_ENABLED", cfg.favorites.enabled)

    return cfg


# --------------------------------------------------------------------------
# YAML parsing
# --------------------------------------------------------------------------


def _merge_site(site: SiteConfig, data: dict[str, Any] | None) -> SiteConfig:
    if not data:
        return site
    return SiteConfig(
        client_id=str(data.get("client_id", site.client_id)),
        client_secret=str(data.get("client_secret", site.client_secret)),
        username=str(data.get("username", site.username)),
        auth_url=str(data.get("auth_url", site.auth_url)),
        token_url=str(data.get("token_url", site.token_url)),
    )


def _config_from_yaml(data: dict[str, Any]) -> Config:
    cfg = Config()

    oauth = data.get("oauth") or {}
    cfg.oauth.port = str(oauth.get("port", cfg.oauth.port))
    cfg.oauth.redirect_uri = str(oauth.get("redirect_uri", cfg.oauth.redirect_uri))

    cfg.anilist = _merge_site(cfg.anilist, data.get("anilist"))
    cfg.myanimelist = _merge_site(cfg.myanimelist, data.get("myanimelist"))

    cfg.token_file_path = str(data.get("token_file_path") or cfg.token_file_path)
    cfg.mappings_file_path = str(data.get("mappings_file_path") or cfg.mappings_file_path)

    watch = data.get("watch") or {}
    cfg.watch.interval = str(watch.get("interval") or cfg.watch.interval)
    cfg.watch.schedule = str(watch.get("schedule") or cfg.watch.schedule)

    cfg.http_timeout = str(data.get("http_timeout") or cfg.http_timeout)

    odb = data.get("offline_database") or {}
    cfg.offline_database.enabled = bool(odb.get("enabled", cfg.offline_database.enabled))
    cfg.offline_database.cache_dir = str(odb.get("cache_dir") or cfg.offline_database.cache_dir)
    cfg.offline_database.auto_update = bool(
        odb.get("auto_update", cfg.offline_database.auto_update)
    )

    arm = data.get("arm_api") or {}
    cfg.arm_api.enabled = bool(arm.get("enabled", cfg.arm_api.enabled))
    cfg.arm_api.base_url = str(arm.get("base_url") or cfg.arm_api.base_url)

    hato = data.get("hato_api") or {}
    cfg.hato_api.enabled = bool(hato.get("enabled", cfg.hato_api.enabled))
    cfg.hato_api.base_url = str(hato.get("base_url") or cfg.hato_api.base_url)
    cfg.hato_api.cache_dir = str(hato.get("cache_dir") or cfg.hato_api.cache_dir)
    cfg.hato_api.cache_max_age = str(hato.get("cache_max_age") or cfg.hato_api.cache_max_age)

    jikan = data.get("jikan_api") or {}
    cfg.jikan_api.enabled = bool(jikan.get("enabled", cfg.jikan_api.enabled))
    cfg.jikan_api.cache_dir = str(jikan.get("cache_dir") or cfg.jikan_api.cache_dir)
    cfg.jikan_api.cache_max_age = str(jikan.get("cache_max_age") or cfg.jikan_api.cache_max_age)

    favorites = data.get("favorites") or {}
    cfg.favorites.enabled = bool(favorites.get("enabled", cfg.favorites.enabled))

    return cfg


# --------------------------------------------------------------------------
# YAML serialization (GUI settings screen; the CLI itself only ever reads
# config.yaml, never writes it)
# --------------------------------------------------------------------------


def _config_to_dict(cfg: Config) -> dict[str, Any]:
    """Mirror _config_from_yaml in reverse. auth_url/token_url are
    deliberately omitted -- they're advanced/rarely-customized fields not
    present in config.example.yaml either, so round-tripping through this
    always leaves them at the DEFAULT_*_URL constants."""
    return {
        "oauth": {"port": cfg.oauth.port, "redirect_uri": cfg.oauth.redirect_uri},
        "anilist": {
            "client_id": cfg.anilist.client_id,
            "client_secret": cfg.anilist.client_secret,
            "username": cfg.anilist.username,
        },
        "myanimelist": {
            "client_id": cfg.myanimelist.client_id,
            "client_secret": cfg.myanimelist.client_secret,
            "username": cfg.myanimelist.username,
        },
        "token_file_path": cfg.token_file_path,
        "mappings_file_path": cfg.mappings_file_path,
        "http_timeout": cfg.http_timeout,
        "watch": {"interval": cfg.watch.interval, "schedule": cfg.watch.schedule},
        "offline_database": {
            "enabled": cfg.offline_database.enabled,
            "cache_dir": cfg.offline_database.cache_dir,
            "auto_update": cfg.offline_database.auto_update,
        },
        "arm_api": {"enabled": cfg.arm_api.enabled, "base_url": cfg.arm_api.base_url},
        "hato_api": {
            "enabled": cfg.hato_api.enabled,
            "base_url": cfg.hato_api.base_url,
            "cache_dir": cfg.hato_api.cache_dir,
            "cache_max_age": cfg.hato_api.cache_max_age,
        },
        "jikan_api": {
            "enabled": cfg.jikan_api.enabled,
            "cache_dir": cfg.jikan_api.cache_dir,
            "cache_max_age": cfg.jikan_api.cache_max_age,
        },
        "favorites": {"enabled": cfg.favorites.enabled},
    }


def save_config(cfg: Config, path: str | os.PathLike[str]) -> None:
    """Write `cfg` to `path` as YAML, same plain-PyYAML-dump approach as
    mapping/manual_mappings.py's MappingsConfig.save(). Used by the GUI's
    Settings screen so a non-technical user never has to hand-edit YAML."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(yaml.safe_dump(_config_to_dict(cfg), sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Validation & top-level entry point
# --------------------------------------------------------------------------


def _config_help(config_path: Path | None) -> str:
    lines = [
        "Configuration not found or incomplete.",
        "",
        "To fix this:",
        "  1. Copy the example config: cp config.example.yaml config.yaml",
        "  2. Edit config.yaml with your AniList and MyAnimeList credentials",
    ]
    if config_path is not None:
        lines.append(f"  3. Run again with: al-mal-sync -c {config_path} ...")
    else:
        lines.append(
            "  3. Or set ANILIST_CLIENT_ID / ANILIST_USERNAME / "
            "MAL_CLIENT_ID / MAL_USERNAME environment variables"
        )
    return "\n".join(lines)


def _validate_required(cfg: Config, config_path: Path | None) -> None:
    missing = []
    if not cfg.anilist.client_id:
        missing.append("anilist.client_id")
    if not cfg.anilist.username:
        missing.append("anilist.username")
    if not cfg.myanimelist.client_id:
        missing.append("myanimelist.client_id")
    if not cfg.myanimelist.username:
        missing.append("myanimelist.username")

    if missing:
        raise ConfigError(
            "required configuration missing: "
            + ", ".join(missing)
            + "\n\n"
            + _config_help(config_path)
        )


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration with priority: env var > config.yaml > built-in default.

    If `path` is omitted, configuration comes entirely from environment variables.
    If `path` is given but the file doesn't exist, falls back to environment
    variables instead of raising. This matches the upstream Go tool's forgiving
    behavior, since Docker/CI setups often rely on env-only config with no file
    present at all.
    """
    if path is None:
        cfg = _apply_env_overrides(Config())
        _validate_required(cfg, None)
        return cfg

    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Not a hard error: fall back to env vars, same as the `path is None` case
        # above. See the docstring for why (Docker/CI setups, mainly).
        cfg = _apply_env_overrides(Config())
        _validate_required(cfg, config_path)
        return cfg

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse config file {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"config file {config_path} must contain a YAML mapping")

    cfg = _config_from_yaml(data)
    cfg = _apply_env_overrides(cfg)
    _validate_required(cfg, config_path)
    return cfg
