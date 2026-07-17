"""OAuth2 authentication: local callback server, token exchange/refresh, token file persistence.

Ported from the reference Go tool's oauth.go. Two services, two auth flows:
AniList uses PKCE with the S256 challenge method, MyAnimeList uses PKCE with the
"plain" method (it doesn't support S256). Both share one token file on disk, keyed
by service name.

Deliberate deviations from the Go version:
  - No context.Context-style cancellation token. This is a synchronous CLI tool, so
    the login flow just blocks on a wait-with-timeout and honors Ctrl+C (KeyboardInterrupt)
    like any other blocking Python call, instead of threading a cancellation object
    through every method.
  - `login()` takes a bounded timeout (default 5 minutes) so an unattended run
    (Docker, CI) can't hang forever waiting for a browser redirect that will never
    come.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import logging
import os
import secrets
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import click
import requests

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_TIMEOUT = 300.0  # seconds; bounds how long `login()` waits for the browser redirect
TOKEN_REFRESH_SKEW = 60.0  # seconds; refresh this long before actual expiry, not exactly at it


class OAuthError(Exception):
    """Raised for OAuth2 flow or token storage failures."""


# --------------------------------------------------------------------------
# PKCE helpers
# --------------------------------------------------------------------------


def _generate_pkce_verifier() -> str:
    # RFC 7636 requires 43-128 chars from [A-Za-z0-9-._~]. token_urlsafe's alphabet
    # (base64url: A-Za-z0-9-_) is a subset of that, so this is always valid.
    return secrets.token_urlsafe(48)


def _pkce_s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --------------------------------------------------------------------------
# Token
# --------------------------------------------------------------------------


@dataclass
class Token:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str = ""
    expiry: float | None = None  # unix epoch seconds; None means unknown/never expires

    def is_expired(self, skew_seconds: float = TOKEN_REFRESH_SKEW) -> bool:
        if self.expiry is None:
            return False
        return time.time() >= (self.expiry - skew_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "refresh_token": self.refresh_token,
            "expiry": self.expiry,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Token:
        return cls(
            access_token=data.get("access_token", ""),
            token_type=data.get("token_type") or "Bearer",
            refresh_token=data.get("refresh_token", ""),
            expiry=data.get("expiry"),
        )

    @classmethod
    def from_token_response(cls, data: dict[str, Any], *, previous_refresh_token: str = "") -> Token:
        expires_in = data.get("expires_in")
        expiry = time.time() + float(expires_in) if expires_in is not None else None
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type") or "Bearer",
            # Some providers omit refresh_token on refresh responses, which means
            # "keep using the one you already have", not "you no longer have one".
            refresh_token=data.get("refresh_token") or previous_refresh_token,
            expiry=expiry,
        )


# --------------------------------------------------------------------------
# Token file persistence
# --------------------------------------------------------------------------


class TokenStore:
    """Reads/writes the shared token JSON file: `{"tokens": {service_name: {...}}}`.

    Multiple OAuth instances (one per service) point at the same file. Each save
    re-reads the file fresh, updates only its own key, and writes the whole file
    back, so instances don't need to share in-memory state. This is safe as long
    as logins happen one at a time, which is how the CLI drives them.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load_all(self) -> dict[str, dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OAuthError(f"failed to parse token file {self.path}: {exc}") from exc
        tokens = data.get("tokens")
        return tokens if isinstance(tokens, dict) else {}

    def load(self, site_name: str) -> Token | None:
        raw = self.load_all().get(site_name)
        return Token.from_dict(raw) if raw else None

    def save(self, site_name: str, token: Token) -> None:
        tokens = self.load_all()
        tokens[site_name] = token.to_dict()
        self._write_all(tokens)

    def delete(self, site_name: str) -> None:
        tokens = self.load_all()
        if site_name in tokens:
            del tokens[site_name]
            self._write_all(tokens)

    def _write_all(self, tokens: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"tokens": tokens}, indent=2)

        # Atomic write: write to a temp file in the same directory, fsync it, then
        # rename over the real path. Avoids a half-written token file if the
        # process dies mid-write.
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix="token", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        # Best-effort permission lockdown. Meaningless on Windows (no POSIX bits)
        # but matters for the common case of running this in a Docker/Linux container.
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)


# --------------------------------------------------------------------------
# Local OAuth callback server
# --------------------------------------------------------------------------


class _CallbackHTTPServer(http.server.HTTPServer):
    """Attributes are attached by OAuth._start_callback_server() after construction;
    the handler reads them back via self.server.*"""

    oauth: OAuth
    done: threading.Event
    outcome: dict[str, Any]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackHTTPServer

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's naming)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = urllib.parse.parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]

        if error:
            self._respond(400, f"Authorization denied: {error}")
            self._finish(error=f"authorization denied: {error}")
            return

        if not state:
            self._respond(400, "State parameter missing")
            return
        if state != self.server.oauth._state:
            self._respond(400, "Invalid state parameter")
            return
        if not code:
            self._respond(400, "Code parameter missing")
            return

        try:
            self.server.oauth.exchange_code(code)
        except OAuthError as exc:
            self._respond(500, "Error exchanging code for token")
            self._finish(error=str(exc))
            return

        self._respond(
            200,
            "<html><body><h2>Authorization successful. You can close this window.</h2>"
            "<script>window.close();</script></body></html>",
            content_type="text/html",
        )
        self._finish()

    def _respond(self, status: int, body: str, content_type: str = "text/plain") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _finish(self, error: str | None = None) -> None:
        if error:
            self.server.outcome["error"] = error
        self.server.done.set()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("callback server: " + format, *args)


def _default_display_auth_url(url: str) -> None:
    click.echo("\nOpen the following URL in your browser:")
    click.secho(url, fg="cyan", bold=True)
    click.echo()


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------


class OAuth:
    """OAuth2 authorization-code flow for one service (AniList or MyAnimeList)."""

    def __init__(
        self,
        site_name: str,
        client_id: str,
        client_secret: str,
        auth_url: str,
        token_url: str,
        redirect_uri: str,
        token_file_path: str,
        *,
        pkce_method: str | None = None,  # "S256", "plain", or None
        extra_auth_params: dict[str, str] | None = None,
        http_timeout: float = 30.0,
    ) -> None:
        token_path = Path(token_file_path)
        if not token_path.is_absolute():
            raise OAuthError(f"token file path must be absolute: {token_file_path}")

        self.site_name = site_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = auth_url
        self.token_url = token_url
        self.redirect_uri = redirect_uri
        self.pkce_method = pkce_method
        self.extra_auth_params = dict(extra_auth_params or {})
        self.http_timeout = http_timeout

        self.token_store = TokenStore(token_file_path)
        self._state = secrets.token_urlsafe(32)
        self._verifier = _generate_pkce_verifier() if pkce_method else None
        self._lock = threading.RLock()

        self.token: Token | None = self.token_store.load(site_name)

    @property
    def needs_init(self) -> bool:
        with self._lock:
            return self.token is None

    @property
    def is_token_valid(self) -> bool:
        with self._lock:
            return self.token is not None and not self.token.is_expired(skew_seconds=0)

    @property
    def token_expiry(self) -> float | None:
        with self._lock:
            return self.token.expiry if self.token else None

    def get_auth_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": self._state,
        }
        params.update(self.extra_auth_params)

        if self.pkce_method == "S256":
            assert self._verifier is not None
            params["code_challenge"] = _pkce_s256_challenge(self._verifier)
            params["code_challenge_method"] = "S256"
        elif self.pkce_method == "plain":
            assert self._verifier is not None
            params["code_challenge"] = self._verifier
            params["code_challenge_method"] = "plain"

        return f"{self.auth_url}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> Token:
        """Exchange an authorization code for a token and persist it."""
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        if self._verifier:
            payload["code_verifier"] = self._verifier

        data = self._post_token_request(payload)
        token = Token.from_token_response(data)
        with self._lock:
            self.token = token
            self.token_store.save(self.site_name, token)
        return token

    def refresh(self) -> Token:
        """Force a token refresh using the stored refresh_token."""
        with self._lock:
            current = self.token
        if current is None or not current.refresh_token:
            raise OAuthError(f"no refresh token available for {self.site_name}")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": current.refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        data = self._post_token_request(payload)
        token = Token.from_token_response(data, previous_refresh_token=current.refresh_token)
        with self._lock:
            self.token = token
            self.token_store.save(self.site_name, token)
        return token

    def get_valid_token(self) -> Token:
        """Return a usable token, refreshing first if it's expired (or nearly so)."""
        with self._lock:
            current = self.token
        if current is None:
            raise OAuthError(f"not authenticated with {self.site_name}, run 'login' first")
        if current.is_expired():
            return self.refresh()
        return current

    def delete_token(self) -> None:
        with self._lock:
            self.token = None
            self.token_store.delete(self.site_name)

    def _post_token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            response = requests.post(
                self.token_url,
                data=payload,
                headers={"Accept": "application/json"},
                timeout=self.http_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OAuthError(f"token request to {self.site_name} failed: {exc}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise OAuthError(f"invalid token response from {self.site_name}: {exc}") from exc

    def _start_callback_server(
        self, port: str
    ) -> tuple[_CallbackHTTPServer, threading.Thread, threading.Event, dict[str, Any]]:
        try:
            server = _CallbackHTTPServer(("", int(port)), _CallbackHandler)
        except (OSError, ValueError) as exc:
            raise OAuthError(f"failed to start callback server on port {port}: {exc}") from exc

        done = threading.Event()
        outcome: dict[str, Any] = {}
        server.oauth = self
        server.done = done
        server.outcome = outcome

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, done, outcome

    def login(
        self,
        port: str,
        *,
        timeout: float | None = DEFAULT_LOGIN_TIMEOUT,
        on_auth_url: Callable[[str], None] | None = None,
    ) -> Token:
        """Run the interactive login flow.

        Starts the local callback server, prints (or hands to `on_auth_url`) the
        URL the user needs to open, blocks until the redirect arrives, and returns
        the resulting token. No-op if already authenticated.
        """
        if not self.needs_init:
            assert self.token is not None
            return self.token

        server, thread, done, outcome = self._start_callback_server(port)
        try:
            display = on_auth_url or _default_display_auth_url
            display(self.get_auth_url())

            if not done.wait(timeout=timeout):
                raise OAuthError(f"timed out waiting for {self.site_name} login callback")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        if outcome.get("error"):
            raise OAuthError(str(outcome["error"]))
        if self.token is None:
            raise OAuthError(f"failed to obtain token for {self.site_name}")

        return self.token


# --------------------------------------------------------------------------
# Service-specific factories
# --------------------------------------------------------------------------


def create_anilist_oauth(config: Config) -> OAuth:
    # AniList's OAuth2 implementation is plain authorization-code grant: just
    # client_id/client_secret/redirect_uri/code, no PKCE and no "offline
    # access" concept (see docs.anilist.co/guide/auth). Sending unsupported
    # params like code_challenge/access_type makes AniList's authorize
    # endpoint reject the request outright with "Invalid Client" rather than
    # ignoring them. AniList also has no refresh-token flow -- access tokens
    # are valid for 1 year and expiry requires a full re-login, not a refresh.
    site = config.anilist
    return OAuth(
        site_name="anilist",
        client_id=site.client_id,
        client_secret=site.client_secret,
        auth_url=site.auth_url,
        token_url=site.token_url,
        redirect_uri=config.oauth.redirect_uri,
        token_file_path=config.resolved_token_file_path,
        http_timeout=config.get_http_timeout().total_seconds(),
    )


def create_myanimelist_oauth(config: Config) -> OAuth:
    # MAL's OAuth implementation only supports the "plain" PKCE method, not S256.
    site = config.myanimelist
    return OAuth(
        site_name="myanimelist",
        client_id=site.client_id,
        client_secret=site.client_secret,
        auth_url=site.auth_url,
        token_url=site.token_url,
        redirect_uri=config.oauth.redirect_uri,
        token_file_path=config.resolved_token_file_path,
        pkce_method="plain",
        http_timeout=config.get_http_timeout().total_seconds(),
    )
