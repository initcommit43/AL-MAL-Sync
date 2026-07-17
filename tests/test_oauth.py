"""Tests for OAuth2 token handling, storage, and the local callback flow."""

from __future__ import annotations

import http.client
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import requests

from al_mal_sync.config import Config
from al_mal_sync.oauth import (
    OAuth,
    OAuthError,
    Token,
    TokenStore,
    create_anilist_oauth,
    create_myanimelist_oauth,
)

ANILIST_AUTH_URL = "https://anilist.co/api/v2/oauth/authorize"
ANILIST_TOKEN_URL = "https://anilist.co/api/v2/oauth/token"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _make_oauth(tmp_path: Path, **overrides: Any) -> OAuth:
    defaults: dict[str, Any] = {
        "site_name": "anilist",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "auth_url": ANILIST_AUTH_URL,
        "token_url": ANILIST_TOKEN_URL,
        "redirect_uri": "http://localhost:18080/callback",
        "token_file_path": str(tmp_path / "token.json"),
        "pkce_method": "S256",
    }
    defaults.update(overrides)
    return OAuth(**defaults)


class TestToken:
    def test_is_expired_none_expiry_never_expires(self) -> None:
        assert Token(access_token="a").is_expired() is False

    def test_is_expired_future_expiry(self) -> None:
        token = Token(access_token="a", expiry=time.time() + 3600)
        assert token.is_expired() is False

    def test_is_expired_past_expiry(self) -> None:
        token = Token(access_token="a", expiry=time.time() - 10)
        assert token.is_expired() is True

    def test_is_expired_within_skew_window(self) -> None:
        # Expires in 30s, but default skew is 60s, so it should count as expired.
        token = Token(access_token="a", expiry=time.time() + 30)
        assert token.is_expired() is True
        assert token.is_expired(skew_seconds=0) is False

    def test_dict_roundtrip(self) -> None:
        token = Token(access_token="a", token_type="Bearer", refresh_token="r", expiry=123.0)
        assert Token.from_dict(token.to_dict()) == token

    def test_from_token_response_with_expiry(self) -> None:
        before = time.time()
        token = Token.from_token_response({"access_token": "a", "expires_in": 100})
        assert token.expiry is not None
        assert before + 100 <= token.expiry <= before + 101

    def test_from_token_response_without_expiry(self) -> None:
        token = Token.from_token_response({"access_token": "a"})
        assert token.expiry is None

    def test_from_token_response_keeps_old_refresh_token_when_omitted(self) -> None:
        token = Token.from_token_response(
            {"access_token": "new"}, previous_refresh_token="old-refresh"
        )
        assert token.refresh_token == "old-refresh"


class TestTokenStore:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        store = TokenStore(str(tmp_path / "token.json"))
        assert store.load_all() == {}
        assert store.load("anilist") is None

    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        store = TokenStore(str(tmp_path / "token.json"))
        token = Token(access_token="a", refresh_token="r", expiry=123.0)
        store.save("anilist", token)
        assert store.load("anilist") == token

    def test_save_preserves_other_services(self, tmp_path: Path) -> None:
        store = TokenStore(str(tmp_path / "token.json"))
        store.save("anilist", Token(access_token="a"))
        store.save("myanimelist", Token(access_token="m"))
        assert store.load("anilist") is not None
        assert store.load("myanimelist") is not None

    def test_delete_removes_only_target_service(self, tmp_path: Path) -> None:
        store = TokenStore(str(tmp_path / "token.json"))
        store.save("anilist", Token(access_token="a"))
        store.save("myanimelist", Token(access_token="m"))
        store.delete("anilist")
        assert store.load("anilist") is None
        assert store.load("myanimelist") is not None

    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "token.json"
        path.write_text("{not valid json", encoding="utf-8")
        store = TokenStore(str(path))
        with pytest.raises(OAuthError, match="failed to parse"):
            store.load_all()


class TestOAuthConstruction:
    def test_rejects_relative_token_path(self) -> None:
        with pytest.raises(OAuthError, match="must be absolute"):
            OAuth(
                site_name="anilist",
                client_id="id",
                client_secret="secret",
                auth_url=ANILIST_AUTH_URL,
                token_url=ANILIST_TOKEN_URL,
                redirect_uri="http://localhost:18080/callback",
                token_file_path="relative/token.json",
            )

    def test_needs_init_true_without_existing_token(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path)
        assert oauth.needs_init is True

    def test_loads_existing_token_from_store(self, tmp_path: Path) -> None:
        token_path = tmp_path / "token.json"
        TokenStore(str(token_path)).save("anilist", Token(access_token="existing"))
        oauth = _make_oauth(tmp_path, token_file_path=str(token_path))
        assert oauth.needs_init is False
        assert oauth.token is not None
        assert oauth.token.access_token == "existing"


class TestGetAuthUrl:
    def test_s256_includes_challenge(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path, pkce_method="S256")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["code_challenge_method"] == ["S256"]
        assert "code_challenge" in query

    def test_plain_uses_verifier_as_challenge(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path, pkce_method="plain")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["code_challenge_method"] == ["plain"]
        assert query["code_challenge"] == [oauth._verifier]

    def test_extra_auth_params_included(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path, extra_auth_params={"access_type": "offline"})
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["access_type"] == ["offline"]


class TestExchangeAndRefresh:
    def test_exchange_code_persists_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = _make_oauth(tmp_path)
        monkeypatch.setattr(
            "al_mal_sync.oauth.requests.post",
            lambda *a, **k: _FakeResponse(200, {"access_token": "new", "expires_in": 3600}),
        )

        token = oauth.exchange_code("auth-code")

        assert token.access_token == "new"
        assert oauth.token == token
        assert TokenStore(oauth.token_store.path.as_posix()).load("anilist") == token

    def test_refresh_uses_refresh_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = _make_oauth(tmp_path)
        oauth.token = Token(access_token="old", refresh_token="refresh-me", expiry=0)

        captured: dict[str, Any] = {}

        def fake_post(url: str, data: dict[str, str], **kwargs: Any) -> _FakeResponse:
            captured.update(data)
            return _FakeResponse(200, {"access_token": "refreshed"})

        monkeypatch.setattr("al_mal_sync.oauth.requests.post", fake_post)

        token = oauth.refresh()

        assert captured["grant_type"] == "refresh_token"
        assert captured["refresh_token"] == "refresh-me"
        assert token.access_token == "refreshed"
        # Response omitted refresh_token, so the old one must carry over.
        assert token.refresh_token == "refresh-me"

    def test_refresh_without_refresh_token_raises(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path)
        oauth.token = Token(access_token="old", refresh_token="")
        with pytest.raises(OAuthError, match="no refresh token"):
            oauth.refresh()

    def test_get_valid_token_without_auth_raises(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path)
        with pytest.raises(OAuthError, match="not authenticated"):
            oauth.get_valid_token()

    def test_get_valid_token_refreshes_when_expired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = _make_oauth(tmp_path)
        oauth.token = Token(access_token="old", refresh_token="r", expiry=time.time() - 10)
        monkeypatch.setattr(
            "al_mal_sync.oauth.requests.post",
            lambda *a, **k: _FakeResponse(200, {"access_token": "fresh"}),
        )

        token = oauth.get_valid_token()

        assert token.access_token == "fresh"

    def test_get_valid_token_returns_as_is_when_valid(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path)
        oauth.token = Token(access_token="still-good", expiry=time.time() + 3600)
        assert oauth.get_valid_token().access_token == "still-good"

    def test_delete_token_clears_memory_and_store(self, tmp_path: Path) -> None:
        oauth = _make_oauth(tmp_path)
        oauth.token = Token(access_token="a")
        oauth.token_store.save("anilist", oauth.token)

        oauth.delete_token()

        assert oauth.needs_init is True
        assert oauth.token_store.load("anilist") is None


class TestLoginFlow:
    def test_callback_rejects_wrong_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = _make_oauth(tmp_path)
        monkeypatch.setattr(
            "al_mal_sync.oauth.requests.post",
            lambda *a, **k: _FakeResponse(200, {"access_token": "should-not-be-used"}),
        )

        server, thread, done, outcome = oauth._start_callback_server("0")
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/callback?state=wrong&code=abc")
            response = conn.getresponse()
            assert response.status == 400
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert oauth.needs_init is True  # exchange must never have been attempted

    def test_callback_completes_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        oauth = _make_oauth(tmp_path)
        monkeypatch.setattr(
            "al_mal_sync.oauth.requests.post",
            lambda *a, **k: _FakeResponse(200, {"access_token": "from-callback"}),
        )

        server, thread, done, outcome = oauth._start_callback_server("0")
        try:
            port = server.server_address[1]
            query = urllib.parse.urlencode({"state": oauth._state, "code": "auth-code"})
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/callback?{query}")
            response = conn.getresponse()
            assert response.status == 200
            assert done.wait(timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert oauth.needs_init is False
        assert oauth.token is not None
        assert oauth.token.access_token == "from-callback"


class TestCreateAniListOAuth:
    """Matches the reference Go tool's newAnilistOAuth (anilist.go): PKCE
    S256 plus access_type=offline. Confirmed against the reference source,
    not just docs.anilist.co (which doesn't mention PKCE at all but doesn't
    mean it's rejected -- the reference tool sends it and works)."""

    def _config(self, tmp_path: Path) -> Config:
        config = Config()
        config.anilist.client_id = "anilist-client-id"
        config.anilist.client_secret = "anilist-client-secret"
        config.token_file_path = str(tmp_path / "token.json")
        return config

    def test_auth_url_uses_s256_pkce(self, tmp_path: Path) -> None:
        oauth = create_anilist_oauth(self._config(tmp_path))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["code_challenge_method"] == ["S256"]
        assert "code_challenge" in query

    def test_auth_url_requests_offline_access(self, tmp_path: Path) -> None:
        oauth = create_anilist_oauth(self._config(tmp_path))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["access_type"] == ["offline"]


class TestCreateMyAnimeListOAuth:
    def _config(self, tmp_path: Path) -> Config:
        config = Config()
        config.myanimelist.client_id = "mal-client-id"
        config.token_file_path = str(tmp_path / "token.json")
        return config

    def test_auth_url_uses_plain_pkce(self, tmp_path: Path) -> None:
        oauth = create_myanimelist_oauth(self._config(tmp_path))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(oauth.get_auth_url()).query)
        assert query["code_challenge_method"] == ["plain"]
        assert query["code_challenge"] == [oauth._verifier]
