"""Login tab: one row per service (AniList, MyAnimeList) showing auth
status, with Login/Logout buttons. Login runs oauth.login() on a worker
thread since it blocks waiting for the OAuth redirect callback; the browser
is opened from on_auth_url (safe to call from the worker thread -- it just
shells out, no widget access).
"""

from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import Callable

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...oauth import OAuth, OAuthError, create_anilist_oauth, create_myanimelist_oauth
from ..workers import run_in_thread

_SERVICES = (("anilist", "AniList"), ("myanimelist", "MyAnimeList"))


def _format_expiry(expiry: float | None) -> str:
    if expiry is None:
        return "never"
    return datetime.fromtimestamp(expiry).isoformat(timespec="seconds")


def _oauth_for(service_key: str, config: Config) -> OAuth:
    return create_anilist_oauth(config) if service_key == "anilist" else create_myanimelist_oauth(config)


class LoginTab(QWidget):
    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
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

        layout = QVBoxLayout(self)
        for service_key, display_name in _SERVICES:
            layout.addWidget(self._build_service_row(service_key, display_name))
        layout.addStretch(1)

        self.refresh_status()

    def _build_service_row(self, service_key: str, display_name: str) -> QGroupBox:
        group = QGroupBox(display_name, self)
        row = QHBoxLayout(group)

        status_label = QLabel("checking...", group)
        self._status_labels[service_key] = status_label
        row.addWidget(status_label, 1)

        login_button = QPushButton("Login", group)
        login_button.clicked.connect(self._make_login_handler(service_key))
        self._login_buttons[service_key] = login_button
        row.addWidget(login_button)

        logout_button = QPushButton("Logout", group)
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

    def refresh_status(self) -> None:
        config = self._get_config()
        for service_key, _display_name in _SERVICES:
            self._refresh_one_status(service_key, config)

    def _refresh_one_status(self, service_key: str, config: Config) -> None:
        label = self._status_labels[service_key]
        try:
            oauth = _oauth_for(service_key, config)
        except OAuthError as exc:
            label.setText(f"config error: {exc}")
            return
        if oauth.needs_init:
            label.setText("not authenticated")
        elif oauth.is_token_valid:
            label.setText(f"authenticated (expires {_format_expiry(oauth.token_expiry)})")
        else:
            label.setText("token expired, will refresh on next use")

    def _on_login_clicked(self, service_key: str) -> None:
        if self._pending_service is not None:
            return  # a login is already in flight
        config = self._get_config()
        oauth = _oauth_for(service_key, config)
        if not oauth.needs_init:
            self._status_labels[service_key].setText("already authenticated")
            return

        self._pending_service = service_key
        self._set_buttons_enabled(False)
        self._status_labels[service_key].setText("waiting for browser login...")

        self._login_thread, self._login_worker = run_in_thread(
            self,
            oauth.login,
            config.oauth.port,
            on_auth_url=webbrowser.open,
            on_finished=self._on_login_finished,
            on_error=self._on_login_error,
        )

    def _on_login_finished(self, _token: object) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        if service_key is not None:
            self._refresh_one_status(service_key, self._get_config())

    def _on_login_error(self, message: str) -> None:
        service_key = self._pending_service
        self._pending_service = None
        self._set_buttons_enabled(True)
        if service_key is not None:
            self._status_labels[service_key].setText(f"login failed: {message}")

    def _on_logout_clicked(self, service_key: str) -> None:
        # Just deletes a local file -- fast enough to run on the GUI thread.
        _oauth_for(service_key, self._get_config()).delete_token()
        self._refresh_one_status(service_key, self._get_config())

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._login_buttons.values():
            button.setEnabled(enabled)
