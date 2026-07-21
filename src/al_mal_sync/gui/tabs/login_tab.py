"""Login page: one row per service (AniList, MyAnimeList) showing auth
status, with exactly one action button visible at a time -- "Log in to..."
when logged out, "Log out" when logged in. Login runs oauth.login() on a
worker thread since it blocks waiting for the OAuth redirect callback; the
browser is opened from on_auth_url (safe to call from the worker thread --
it just shells out, no widget access).

Right after a successful login, and only if Settings doesn't already have a
username for that service, the same worker thread also fetches the
authenticated user's own username via the API and saves it to config.yaml.
Without this, a user who only ever used OAuth login had no way to know
Settings' separate username field even existed, let alone that the
Dashboard/Sync silently need it filled in -- the field is a real, load-
bearing requirement of the AniList/MyAnimeList clients (they list a
*named* user's entries), just not one OAuth login satisfies by itself.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...clients.anilist import AniListAPIError, AniListClient
from ...clients.myanimelist import MyAnimeListAPIError, MyAnimeListClient
from ...config import Config, ConfigError, save_config
from ...oauth import OAuth, OAuthError, create_anilist_oauth, create_myanimelist_oauth
from ..theme import DANGER, SUCCESS
from ..widgets import apply_page_layout
from ..workers import run_in_thread

_SERVICES = (("anilist", "AniList"), ("myanimelist", "MyAnimeList"))


def _format_expiry(expiry: float | None) -> str:
    if expiry is None:
        return "never"
    return datetime.fromtimestamp(expiry).isoformat(timespec="seconds")


def _oauth_for(service_key: str, config: Config) -> OAuth:
    return create_anilist_oauth(config) if service_key == "anilist" else create_myanimelist_oauth(config)


def _site_config(service_key: str, config: Config) -> object:
    return config.anilist if service_key == "anilist" else config.myanimelist


def _fetch_username(service_key: str, oauth: OAuth, config: Config) -> str | None:
    """Best-effort: returns None (never raises) on any API failure -- callers
    treat "couldn't fetch" the same as "fetch not attempted" rather than
    surfacing it as a hard error, since the username field is the only thing
    this affects and the user can always fall back to typing it in Settings."""
    http_timeout = config.get_http_timeout().total_seconds()
    try:
        if service_key == "anilist":
            return AniListClient(oauth, "", http_timeout=http_timeout).get_authenticated_username()
        return MyAnimeListClient(oauth, "", http_timeout=http_timeout).get_authenticated_username()
    except (AniListAPIError, MyAnimeListAPIError):
        return None


def _login_and_fetch_username(
    oauth: OAuth, port: str, service_key: str, config: Config, on_auth_url: Callable[[str], None]
) -> tuple[object, str | None]:
    """Worker-thread body: completes the interactive OAuth login, then
    (only if Settings doesn't already have a username for this service)
    fetches the authenticated user's own username via the API. Best-effort
    -- a failed/unreachable username fetch never fails the login itself,
    since the login already succeeded by that point."""
    token = oauth.login(port, on_auth_url=on_auth_url)
    if getattr(_site_config(service_key, config), "username", ""):
        return token, None
    return token, _fetch_username(service_key, oauth, config)


def _fetch_username_only(oauth: OAuth, service_key: str, config: Config) -> str | None:
    """Worker-thread body for the explicit "Fetch my username" button --
    same lookup as _login_and_fetch_username, without the login step, for a
    service that's already authenticated but has no username saved yet (a
    user who logged in before this auto-fetch existed, or who deleted the
    username from Settings)."""
    return _fetch_username(service_key, oauth, config)


class LoginTab(QWidget):
    # Emitted after a successful login or logout, so other pages (the
    # Dashboard) know to refresh their own view of auth state.
    auth_changed = Signal()

    def __init__(
        self, get_config: Callable[[], Config], config_path: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._config_path = config_path
        # Context for whichever login is currently in flight -- read back
        # inside _on_login_finished/_on_login_error, which (per workers.py's
        # contract) must be real bound methods with no per-call args curried
        # in via lambda/partial, since that would break the GUI-thread
        # delivery of the worker's signal.
        self._pending_service: str | None = None
        # Keeps the (QThread, Worker) pair alive for the duration of a login
        # -- an uncaptured/garbage-collected pair is how a worker silently
        # never runs.
        self._login_thread = None
        self._login_worker = None

        self._status_labels: dict[str, QLabel] = {}
        self._login_buttons: dict[str, QPushButton] = {}
        self._logout_buttons: dict[str, QPushButton] = {}
        self._fetch_username_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        title = QLabel("Log in", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Connect your AniList and MyAnimeList accounts. Both are needed before you can sync.", self
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        for service_key, display_name in _SERVICES:
            layout.addWidget(self._build_service_row(service_key, display_name))
        layout.addStretch(1)

        self.refresh_status()

    def _build_service_row(self, service_key: str, display_name: str) -> QGroupBox:
        group = QGroupBox(display_name, self)
        row = QHBoxLayout(group)

        status_label = QLabel("checking...", group)
        status_label.setWordWrap(True)
        self._status_labels[service_key] = status_label
        row.addWidget(status_label, 1)

        login_button = QPushButton(f"Log in to {display_name}", group)
        login_button.setObjectName("primaryButton")
        login_button.clicked.connect(self._make_login_handler(service_key))
        self._login_buttons[service_key] = login_button
        row.addWidget(login_button)

        fetch_username_button = QPushButton("Fetch my username", group)
        fetch_username_button.setToolTip(
            "Looks up your username via the API and saves it to Settings -- needed once so\n"
            "the Dashboard and Sync know whose AniList/MyAnimeList list to read."
        )
        fetch_username_button.clicked.connect(self._make_fetch_username_handler(service_key))
        fetch_username_button.setVisible(False)
        self._fetch_username_buttons[service_key] = fetch_username_button
        row.addWidget(fetch_username_button)

        logout_button = QPushButton("Log out", group)
        logout_button.setObjectName("dangerButton")
        logout_button.clicked.connect(self._make_logout_handler(service_key))
        self._logout_buttons[service_key] = logout_button
        row.addWidget(logout_button)

        return group

    # Button clicks fire on the GUI thread already, so a small closure here
    # (unlike a worker-thread callback) is safe -- it never crosses threads.
    def _make_login_handler(self, service_key: str) -> Callable[[], None]:
        return lambda: self._on_login_clicked(service_key)

    def _make_logout_handler(self, service_key: str) -> Callable[[], None]:
        return lambda: self._on_logout_clicked(service_key)

    def _make_fetch_username_handler(self, service_key: str) -> Callable[[], None]:
        return lambda: self._on_fetch_username_clicked(service_key)

    def refresh_status(self) -> None:
        config = self._get_config()
        for service_key, _display_name in _SERVICES:
            self._refresh_one_status(service_key, config)

    def _refresh_one_status(self, service_key: str, config: Config) -> None:
        label = self._status_labels[service_key]
        login_button = self._login_buttons[service_key]
        logout_button = self._logout_buttons[service_key]
        fetch_username_button = self._fetch_username_buttons[service_key]
        try:
            oauth = _oauth_for(service_key, config)
        except OAuthError as exc:
            label.setText(f"Config error: {exc}")
            label.setStyleSheet(f"color: {DANGER};")
            login_button.setVisible(False)
            logout_button.setVisible(False)
            fetch_username_button.setVisible(False)
            return

        if oauth.needs_init:
            label.setText("Not logged in.")
            label.setStyleSheet(f"color: {DANGER};")
            login_button.setVisible(True)
            logout_button.setVisible(False)
            fetch_username_button.setVisible(False)
        else:
            if oauth.is_token_valid:
                label.setText(f"Logged in (session valid until {_format_expiry(oauth.token_expiry)}).")
            else:
                label.setText("Logged in (session will refresh automatically next time it's used).")
            label.setStyleSheet(f"color: {SUCCESS};")
            login_button.setVisible(False)
            logout_button.setVisible(True)
            # Authenticated but no username saved yet -- either an older
            # login from before this button existed, or the field was
            # cleared in Settings. Surface a one-click fix instead of only
            # a Settings hint the user might not connect to "no data".
            has_username = bool(getattr(_site_config(service_key, config), "username", ""))
            fetch_username_button.setVisible(not has_username)

    def _on_login_clicked(self, service_key: str) -> None:
        if self._pending_service is not None:
            return  # a login is already in flight
        config = self._get_config()
        oauth = _oauth_for(service_key, config)
        if not oauth.needs_init:
            return

        self._pending_service = service_key
        self._set_buttons_enabled(False)
        self._status_labels[service_key].setStyleSheet("")
        self._status_labels[service_key].setText("Waiting for you to finish logging in in your browser...")

        self._login_thread, self._login_worker = run_in_thread(
            self,
            _login_and_fetch_username,
            oauth,
            config.oauth.port,
            service_key,
            config,
            webbrowser.open,
            on_finished=self._on_login_finished,
            on_error=self._on_login_error,
        )

    def _on_login_finished(self, result: object) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        _token, username = result  # type: ignore[misc]
        if service_key is not None and username:
            self._save_fetched_username(service_key, username)
        if service_key is not None:
            self._refresh_one_status(service_key, self._get_config())
        self.auth_changed.emit()

    def _save_fetched_username(self, service_key: str, username: str) -> None:
        config = self._get_config()
        site = config.anilist if service_key == "anilist" else config.myanimelist
        site.username = username
        try:
            save_config(config, self._config_path)
        except (ConfigError, OSError):
            pass  # best-effort -- the in-memory Config is already updated either way

    def _on_login_error(self, message: str) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        if service_key is not None:
            label = self._status_labels[service_key]
            label.setText(f"Login failed: {message}")
            label.setStyleSheet(f"color: {DANGER};")

    def _on_logout_clicked(self, service_key: str) -> None:
        # Just deletes a local file -- fast enough to run on the GUI thread.
        _oauth_for(service_key, self._get_config()).delete_token()
        self._refresh_one_status(service_key, self._get_config())
        self.auth_changed.emit()

    def _on_fetch_username_clicked(self, service_key: str) -> None:
        if self._pending_service is not None:
            return  # a login or another fetch is already in flight
        config = self._get_config()
        oauth = _oauth_for(service_key, config)
        if oauth.needs_init:
            return

        self._pending_service = service_key
        self._set_buttons_enabled(False)

        self._login_thread, self._login_worker = run_in_thread(
            self,
            _fetch_username_only,
            oauth,
            service_key,
            config,
            on_finished=self._on_fetch_username_finished,
            on_error=self._on_fetch_username_error,
        )

    def _on_fetch_username_finished(self, username: object) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        if service_key is not None:
            if username:
                self._save_fetched_username(service_key, username)
                self.auth_changed.emit()
            else:
                label = self._status_labels[service_key]
                label.setText(f"{label.text()} (couldn't look up your username automatically.)")
            self._refresh_one_status(service_key, self._get_config())

    def _on_fetch_username_error(self, message: str) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        if service_key is not None:
            label = self._status_labels[service_key]
            label.setText(f"{label.text()} (username lookup failed: {message})")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._login_buttons.values():
            button.setEnabled(enabled)
        for button in self._fetch_username_buttons.values():
            button.setEnabled(enabled)
