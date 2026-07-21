"""Tests for gui/tabs/login_tab.py's status display, single-visible-button
behavior, and logout flow. token_file_path always points at tmp_path --
never the user's real stored credentials. The interactive login flow (opens
a real browser, waits for an OAuth redirect) isn't something a unit test can
drive; only the parts that don't require a live browser round-trip are
covered here."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from al_mal_sync.clients.anilist import AniListAPIError  # noqa: E402
from al_mal_sync.config import Config  # noqa: E402
from al_mal_sync.gui.tabs import login_tab as login_tab_module  # noqa: E402
from al_mal_sync.gui.tabs.login_tab import LoginTab, _login_and_fetch_username  # noqa: E402

from .conftest import wait_until  # noqa: E402

# qt_app fixture is shared from conftest.py.


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.token_file_path = str(tmp_path / "token.json")
    return cfg


class _StubOAuth:
    def __init__(self, token: str = "tok") -> None:
        self._token = token

    def login(self, port: str, *, on_auth_url: object = None) -> str:
        return self._token

    def get_valid_token(self) -> None:
        raise AssertionError("should not be called when a username is already set")


class TestLoginAndFetchUsername:
    def test_skips_fetch_when_username_already_set(self) -> None:
        config = Config()
        config.anilist.username = "AlreadySet"

        token, username = _login_and_fetch_username(
            _StubOAuth(), "8080", "anilist", config, lambda url: None
        )

        assert token == "tok"
        assert username is None

    def test_fetches_username_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = Config()

        class _FakeAniListClient:
            def __init__(self, oauth: object, username: str, *, http_timeout: float) -> None:
                pass

            def get_authenticated_username(self) -> str:
                return "FetchedUser"

        monkeypatch.setattr(login_tab_module, "AniListClient", _FakeAniListClient)

        token, username = _login_and_fetch_username(
            _StubOAuth(), "8080", "anilist", config, lambda url: None
        )

        assert token == "tok"
        assert username == "FetchedUser"

    def test_fetch_failure_does_not_fail_the_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = Config()

        class _FailingAniListClient:
            def __init__(self, oauth: object, username: str, *, http_timeout: float) -> None:
                pass

            def get_authenticated_username(self) -> str:
                raise AniListAPIError("boom")

        monkeypatch.setattr(login_tab_module, "AniListClient", _FailingAniListClient)

        token, username = _login_and_fetch_username(
            _StubOAuth(), "8080", "anilist", config, lambda url: None
        )

        assert token == "tok"
        assert username is None


class TestLoginTab:
    def test_shows_not_logged_in_with_no_token_file(self, qt_app: QApplication, config: Config) -> None:
        tab = LoginTab(lambda: config, "unused.yaml")

        assert "not logged in" in tab._status_labels["anilist"].text().lower()
        assert "not logged in" in tab._status_labels["myanimelist"].text().lower()

    def test_not_logged_in_shows_only_login_button(self, qt_app: QApplication, config: Config) -> None:
        tab = LoginTab(lambda: config, "unused.yaml")

        assert tab._login_buttons["anilist"].isHidden() is False
        assert tab._logout_buttons["anilist"].isHidden() is True

    def test_login_button_disabled_while_a_login_is_in_flight(
        self, qt_app: QApplication, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        release = threading.Event()
        tab = LoginTab(lambda: config, "unused.yaml")

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
        tab = LoginTab(lambda: config, "unused.yaml")
        tab._pending_service = "anilist"

        calls: list[str] = []
        monkeypatch.setattr(
            "al_mal_sync.gui.tabs.login_tab._oauth_for",
            lambda key, cfg: calls.append(key) or object(),
        )
        tab._on_login_clicked("myanimelist")

        assert calls == []

    def test_fetch_username_button_shown_only_when_authenticated_without_a_username(
        self, qt_app: QApplication, config: Config
    ) -> None:
        from al_mal_sync.oauth import create_anilist_oauth

        create_anilist_oauth(config).delete_token()
        Path(config.token_file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.token_file_path).write_text(
            '{"tokens": {"anilist": {"access_token": "x", "refresh_token": "y", '
            '"expires_at": 9999999999}}}',
            encoding="utf-8",
        )

        tab = LoginTab(lambda: config, "unused.yaml")

        assert tab._fetch_username_buttons["anilist"].isHidden() is False
        # myanimelist has neither a token nor a username -- not logged in at
        # all, so the button (which only makes sense once authenticated)
        # must stay hidden rather than showing for every missing username.
        assert tab._fetch_username_buttons["myanimelist"].isHidden() is True

        config.anilist.username = "AlreadySet"
        tab._refresh_one_status("anilist", config)

        assert tab._fetch_username_buttons["anilist"].isHidden() is True

    def test_fetch_username_button_click_saves_the_result(
        self, qt_app: QApplication, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from al_mal_sync.oauth import create_anilist_oauth

        create_anilist_oauth(config).delete_token()
        Path(config.token_file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.token_file_path).write_text(
            '{"tokens": {"anilist": {"access_token": "x", "refresh_token": "y", '
            '"expires_at": 9999999999}}}',
            encoding="utf-8",
        )
        monkeypatch.setattr(login_tab_module, "_fetch_username_only", lambda oauth, key, cfg: "ClickedUser")

        config_path = str(tmp_path / "config.yaml")
        tab = LoginTab(lambda: config, config_path)
        assert tab._fetch_username_buttons["anilist"].isHidden() is False

        tab._fetch_username_buttons["anilist"].click()
        wait_until(qt_app, lambda: config.anilist.username == "ClickedUser")

        assert tab._fetch_username_buttons["anilist"].isHidden() is True

    def test_logout_clears_status_back_to_not_logged_in(self, qt_app: QApplication, config: Config) -> None:
        from al_mal_sync.oauth import create_anilist_oauth

        oauth = create_anilist_oauth(config)
        oauth.delete_token()  # ensure clean slate, then simulate a stored token
        Path(config.token_file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.token_file_path).write_text(
            '{"tokens": {"anilist": {"access_token": "x", "refresh_token": "y", '
            '"expires_at": 9999999999}}}',
            encoding="utf-8",
        )

        tab = LoginTab(lambda: config, "unused.yaml")
        assert "logged in" in tab._status_labels["anilist"].text().lower()
        assert tab._logout_buttons["anilist"].isHidden() is False
        assert tab._login_buttons["anilist"].isHidden() is True

        tab._on_logout_clicked("anilist")

        assert "not logged in" in tab._status_labels["anilist"].text().lower()
        assert tab._login_buttons["anilist"].isHidden() is False
        assert tab._logout_buttons["anilist"].isHidden() is True

    def test_logout_emits_auth_changed(self, qt_app: QApplication, config: Config) -> None:
        tab = LoginTab(lambda: config, "unused.yaml")
        calls = []
        tab.auth_changed.connect(lambda: calls.append(1))

        tab._on_logout_clicked("anilist")

        assert calls == [1]

    def test_login_finished_emits_auth_changed(self, qt_app: QApplication, config: Config) -> None:
        tab = LoginTab(lambda: config, "unused.yaml")
        calls = []
        tab.auth_changed.connect(lambda: calls.append(1))

        tab._pending_service = "anilist"
        tab._on_login_finished((object(), None))

        assert calls == [1]

    def test_login_finished_saves_fetched_username_when_settings_had_none(
        self, qt_app: QApplication, config: Config, tmp_path: Path
    ) -> None:
        """A user who only ever used OAuth login has no username in Settings
        yet -- the AniList/MyAnimeList clients need one (they list a *named*
        user's entries), so the worker thread fetches and saves it here
        instead of leaving the Dashboard/Sync silently broken."""
        config_path = str(tmp_path / "config.yaml")
        tab = LoginTab(lambda: config, config_path)
        tab._pending_service = "anilist"

        tab._on_login_finished((object(), "FetchedUser"))

        assert config.anilist.username == "FetchedUser"
        import yaml

        saved = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        assert saved["anilist"]["username"] == "FetchedUser"

    def test_login_finished_does_not_overwrite_an_existing_username(
        self, qt_app: QApplication, config: Config, tmp_path: Path
    ) -> None:
        config.anilist.username = "ManuallyTypedUser"
        tab = LoginTab(lambda: config, str(tmp_path / "config.yaml"))
        tab._pending_service = "anilist"

        # A worker result of (token, None) is exactly what
        # _login_and_fetch_username returns when Settings already had a
        # username -- it skips the API call entirely rather than fetching a
        # value that would just be discarded.
        tab._on_login_finished((object(), None))

        assert config.anilist.username == "ManuallyTypedUser"
