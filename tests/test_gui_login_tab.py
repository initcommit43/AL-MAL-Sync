"""Tests for gui/tabs/login_tab.py's status display and logout flow.
token_file_path always points at tmp_path -- never the user's real stored
credentials. The interactive login flow (opens a real browser, waits for an
OAuth redirect) isn't something a unit test can drive; only the parts that
don't require a live browser round-trip are covered here."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs.login_tab import LoginTab  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.token_file_path = str(tmp_path / "token.json")
    return cfg


class TestLoginTab:
    def test_shows_not_authenticated_with_no_token_file(
        self, qt_app: QApplication, config: Config
    ) -> None:
        tab = LoginTab(lambda: config)

        assert "not authenticated" in tab._status_labels["anilist"].text()
        assert "not authenticated" in tab._status_labels["myanimelist"].text()

    def test_login_button_disabled_while_a_login_is_in_flight(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        release = threading.Event()
        tab = LoginTab(lambda: config)

        class _BlockingOAuth:
            needs_init = True

            def login(self, *a: object, **kw: object) -> None:
                release.wait(timeout=5)

        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.login_tab._oauth_for", lambda key, cfg: _BlockingOAuth()
        )
        try:
            tab._on_login_clicked("anilist")

            assert tab._login_buttons["anilist"].isEnabled() is False
            assert tab._login_buttons["myanimelist"].isEnabled() is False
            assert tab._pending_service == "anilist"
        finally:
            # Unblock the worker thread. Its finished signal only actually
            # gets delivered once the GUI thread's event queue is pumped --
            # a blind thread.wait() would hang forever since nothing here
            # calls app.exec(). wait_until also settles the queue afterward
            # so this thread's deleteLater() cleanup doesn't leak into the
            # next test (see its docstring in conftest.py).
            release.set()
            wait_until(qt_app, lambda: tab._login_thread is None or tab._login_thread.isFinished())

    def test_second_login_click_while_pending_is_ignored(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tab = LoginTab(lambda: config)
        tab._pending_service = "anilist"

        calls: list[str] = []
        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.login_tab._oauth_for",
            lambda key, cfg: calls.append(key) or object(),
        )
        tab._on_login_clicked("myanimelist")

        assert calls == []

    def test_logout_clears_status_back_to_not_authenticated(
        self, qt_app: QApplication, config: Config
    ) -> None:
        from al_mal_sync.oauth import create_anilist_oauth

        oauth = create_anilist_oauth(config)
        oauth.delete_token()  # ensure clean slate, then simulate a stored token
        Path(config.token_file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.token_file_path).write_text(
            '{"tokens": {"anilist": {"access_token": "x", "refresh_token": "y", '
            '"expires_at": 9999999999}}}',
            encoding="utf-8",
        )

        tab = LoginTab(lambda: config)
        assert "authenticated" in tab._status_labels["anilist"].text()

        tab._on_logout_clicked("anilist")

        assert "not authenticated" in tab._status_labels["anilist"].text()
