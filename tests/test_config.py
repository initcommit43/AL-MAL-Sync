"""Tests for config loading: env-only, YAML file, env-overrides-file priority."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from al_mal_sync.config import (
    Config,
    ConfigError,
    WatchConfig,
    default_config_path,
    load_config,
    parse_duration,
    save_config,
)

REQUIRED_ENV = {
    "ANILIST_CLIENT_ID": "ani_id",
    "ANILIST_USERNAME": "ani_user",
    "MAL_CLIENT_ID": "mal_id",
    "MAL_USERNAME": "mal_user",
}

VALID_YAML = """
oauth:
  port: "18080"
  redirect_uri: "http://localhost:18080/callback"
anilist:
  client_id: "file_ani_id"
  client_secret: "file_ani_secret"
  username: "file_ani_user"
myanimelist:
  client_id: "file_mal_id"
  client_secret: "file_mal_secret"
  username: "file_mal_user"
"""


def _set_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


class TestParseDuration:
    def test_simple_hours(self) -> None:
        assert parse_duration("12h") == timedelta(hours=12)

    def test_combined_units(self) -> None:
        assert parse_duration("1h30m") == timedelta(hours=1, minutes=30)

    @pytest.mark.parametrize("value", ["", "abc", "12h!"])
    def test_invalid_raises(self, value: str) -> None:
        with pytest.raises(ConfigError):
            parse_duration(value)


class TestWatchConfigValidate:
    def test_both_set_is_error(self) -> None:
        watch = WatchConfig(interval="12h", schedule="0 3 * * *")
        with pytest.raises(ConfigError, match="not both"):
            watch.validate()

    def test_neither_set_is_error(self) -> None:
        with pytest.raises(ConfigError, match="requires either"):
            WatchConfig().validate()

    def test_interval_too_short(self) -> None:
        with pytest.raises(ConfigError, match="at least 1h"):
            WatchConfig(interval="30m").validate()

    def test_interval_too_long(self) -> None:
        with pytest.raises(ConfigError, match="at most 168h"):
            WatchConfig(interval="200h").validate()

    def test_valid_interval(self) -> None:
        WatchConfig(interval="24h").validate()  # no raise

    def test_valid_schedule_shape(self) -> None:
        WatchConfig(schedule="0 3 * * *").validate()  # no raise

    def test_invalid_schedule_shape(self) -> None:
        with pytest.raises(ConfigError, match="expected 5 fields"):
            WatchConfig(schedule="0 3 * *").validate()

    def test_get_interval(self) -> None:
        assert WatchConfig().get_interval() is None
        assert WatchConfig(interval="2h").get_interval() == timedelta(hours=2)


class TestLoadConfigFromEnv:
    def test_missing_required_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANILIST_CLIENT_ID", raising=False)
        with pytest.raises(ConfigError, match="required configuration missing"):
            load_config(None)

    def test_loads_from_env_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        cfg = load_config(None)
        assert cfg.anilist.client_id == "ani_id"
        assert cfg.anilist.username == "ani_user"
        assert cfg.myanimelist.client_id == "mal_id"
        assert cfg.myanimelist.username == "mal_user"
        assert cfg.anilist.auth_url == "https://anilist.co/api/v2/oauth/authorize"

    def test_client_secret_alias_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        monkeypatch.setenv("CLIENT_SECRET_ANILIST", "aliased_secret")
        cfg = load_config(None)
        assert cfg.anilist.client_secret == "aliased_secret"

    def test_port_falls_back_to_generic_port_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        monkeypatch.setenv("PORT", "9999")
        cfg = load_config(None)
        assert cfg.oauth.port == "9999"

    def test_offline_database_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        cfg = load_config(None)
        assert cfg.offline_database.enabled is True
        assert cfg.hato_api.enabled is True
        assert cfg.arm_api.enabled is False
        assert cfg.jikan_api.enabled is False
        assert cfg.favorites.enabled is False

    def test_bool_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        monkeypatch.setenv("ARM_API_ENABLED", "true")
        monkeypatch.setenv("HATO_API_ENABLED", "false")
        cfg = load_config(None)
        assert cfg.arm_api.enabled is True
        assert cfg.hato_api.enabled is False


class TestLoadConfigFromFile:
    def test_loads_values_from_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANILIST_CLIENT_ID", raising=False)
        monkeypatch.delenv("CLIENT_SECRET_ANILIST", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML, encoding="utf-8")

        cfg = load_config(config_path)

        assert cfg.oauth.port == "18080"
        assert cfg.anilist.client_id == "file_ani_id"
        assert cfg.myanimelist.client_id == "file_mal_id"

    def test_missing_file_falls_back_to_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_env(monkeypatch, REQUIRED_ENV)
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.anilist.client_id == "ani_id"

    def test_missing_file_and_missing_env_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in REQUIRED_ENV:
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{not: valid: yaml: [", encoding="utf-8")
        with pytest.raises(ConfigError, match="failed to parse"):
            load_config(config_path)

    def test_env_overrides_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML, encoding="utf-8")
        monkeypatch.setenv("CLIENT_SECRET_ANILIST", "env_secret")
        monkeypatch.setenv("PORT", "7777")

        cfg = load_config(config_path)

        assert cfg.anilist.client_secret == "env_secret"
        assert cfg.oauth.port == "7777"
        # Values not overridden by env still come from the file.
        assert cfg.anilist.client_id == "file_ani_id"

    def test_non_mapping_yaml_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="must contain a YAML mapping"):
            load_config(config_path)


class TestLoadConfigValidateFalse:
    """validate=False is what the GUI uses (see main_window.py's
    _load_initial_config) so a partially-filled config.yaml -- e.g. just a
    username saved by the Login page's "Fetch my username" button, with no
    client_id yet -- survives a restart instead of being discarded back to a
    blank Config() by the CLI's all-required-fields gate."""

    def test_partial_yaml_does_not_raise_and_keeps_what_was_there(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in REQUIRED_ENV:
            monkeypatch.delenv(key, raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "anilist:\n  username: fetched_user\n", encoding="utf-8"
        )

        cfg = load_config(config_path, validate=False)

        assert cfg.anilist.username == "fetched_user"
        assert cfg.anilist.client_id == ""

    def test_missing_file_and_missing_env_returns_blank_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in REQUIRED_ENV:
            monkeypatch.delenv(key, raising=False)

        cfg = load_config(tmp_path / "nonexistent.yaml", validate=False)

        assert cfg.anilist.username == ""

    def test_invalid_yaml_still_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{not: valid: yaml: [", encoding="utf-8")

        with pytest.raises(ConfigError, match="failed to parse"):
            load_config(config_path, validate=False)


_ALL_CONFIG_ENV_KEYS = [
    "OAUTH_PORT", "PORT", "OAUTH_REDIRECT_URI",
    "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "CLIENT_SECRET_ANILIST", "ANILIST_USERNAME",
    "MAL_CLIENT_ID", "MAL_CLIENT_SECRET", "CLIENT_SECRET_MYANIMELIST", "MAL_USERNAME",
    "TOKEN_FILE_PATH", "MAPPINGS_FILE_PATH", "WATCH_INTERVAL", "WATCH_SCHEDULE", "HTTP_TIMEOUT",
    "OFFLINE_DATABASE_ENABLED", "OFFLINE_DATABASE_CACHE_DIR", "OFFLINE_DATABASE_AUTO_UPDATE",
    "ARM_API_ENABLED", "ARM_API_URL",
    "HATO_API_ENABLED", "HATO_API_URL", "HATO_API_CACHE_DIR", "HATO_API_CACHE_MAX_AGE",
    "JIKAN_API_ENABLED", "JIKAN_API_CACHE_DIR", "JIKAN_API_CACHE_MAX_AGE",
    "FAVORITES_SYNC_ENABLED",
]


class TestSaveConfig:
    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in _ALL_CONFIG_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    def test_round_trips_through_load_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_env(monkeypatch)
        cfg = Config()
        cfg.anilist.client_id = "ani_id"
        cfg.anilist.client_secret = "ani_secret"
        cfg.anilist.username = "ani_user"
        cfg.myanimelist.client_id = "mal_id"
        cfg.myanimelist.client_secret = "mal_secret"
        cfg.myanimelist.username = "mal_user"
        cfg.watch.interval = "6h"
        cfg.arm_api.enabled = True
        cfg.jikan_api.enabled = True
        cfg.favorites.enabled = True

        config_path = tmp_path / "config.yaml"
        save_config(cfg, config_path)
        loaded = load_config(config_path)

        assert loaded == cfg

    def test_creates_parent_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._clear_env(monkeypatch)
        config_path = tmp_path / "nested" / "dir" / "config.yaml"
        save_config(Config(), config_path)
        assert config_path.exists()


class TestDefaultConfigPath:
    def test_ends_with_config_yaml(self) -> None:
        assert default_config_path().endswith("config.yaml")


class TestConfigResolvedPaths:
    def test_resolved_paths_fall_back_to_defaults_when_unset(self) -> None:
        cfg = Config()
        assert cfg.resolved_token_file_path.endswith("token.json")
        assert cfg.resolved_offline_db_cache_dir.endswith("aod-cache")
        assert cfg.resolved_hato_cache_dir.endswith("hato-cache")
        assert cfg.resolved_jikan_cache_dir.endswith("jikan-cache")

    def test_resolved_path_respects_explicit_value(self) -> None:
        cfg = Config(token_file_path="/custom/token.json")
        assert cfg.resolved_token_file_path == "/custom/token.json"

    def test_get_http_timeout_falls_back_on_invalid(self) -> None:
        assert Config(http_timeout="bogus").get_http_timeout() == timedelta(seconds=30)
