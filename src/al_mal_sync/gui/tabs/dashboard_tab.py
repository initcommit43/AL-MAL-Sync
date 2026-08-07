"""Dashboard page: the first thing a user sees. A quick-glance summary --
how big each platform's library is (one stat card per platform, anime and
manga counts together, since "AniList vs MyAnimeList" is the comparison a
user actually cares about here, not "anime vs manga"), whether both accounts
are connected, and whether anything needs attention -- with a "Review" jump
straight to Mapping Issues, since that one's conditional (only appears when
there's actually something to act on). No AniList/MyAnimeList numeric IDs
anywhere here; those only matter on the Mapping Issues page where they're
actually actionable.

Deliberately has no "Go to Sync"/"Go to Login" buttons -- those pages are
already one click away in the always-visible sidebar, so a second button here
pointing at the same place was pure redundancy, not a shortcut.

Counts are fetched live (dashboard.fetch_dashboard_stats) on a worker thread,
same run_in_thread pattern as every other page's network calls. The unmapped
count and last-sync summary are cheap local file reads, done synchronously.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...dashboard import DashboardStats, PlatformStatus, fetch_dashboard_stats
from ...sync_history import load_last_sync
from ...unmapped import load_unmapped_state
from ..theme import DANGER, SUCCESS, WARNING
from ..widgets import Pill, StatCard, apply_page_layout
from ..workers import run_in_thread

# AniList/MyAnimeList both rate-limit aggressively. The Dashboard's own live
# fetch is wired to fire on every nav switch to it, plus after settings
# saves/logins/logouts/syncs (see main_window.py) -- without a staleness
# guard, quick navigation around the app fires a fresh real API call each
# time and trips rate limiting almost immediately. A manual Refresh click
# always bypasses this and fetches now.
_MIN_AUTO_REFRESH_INTERVAL_SECONDS = 20.0


class DashboardTab(QWidget):
    navigate_requested = Signal(str)  # "mapping_issues"

    def __init__(self, get_config: Callable[[], Config], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_config = get_config
        self._thread = None
        self._worker = None
        self._last_fetch_at = 0.0

        layout = QVBoxLayout(self)
        apply_page_layout(layout)
        header = QHBoxLayout()
        title = QLabel("Dashboard", self)
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        layout.addWidget(self._build_accounts_group())

        stats_layout = QGridLayout()
        stats_layout.setSpacing(16)
        self.anilist_card = StatCard("AniList Library size", ["Anime", "Manga"], self)
        self.mal_card = StatCard("MyAnimeList Library size", ["Anime", "Manga"], self)
        # StatCard's own fixed-width value column keeps the two cards equal
        # width when it's the *counts* that differ, but "MyAnimeList Library
        # size" is simply longer text than "AniList Library size" -- without
        # normalizing to the wider card's natural width here too, the titles
        # alone would make the cards visibly mismatched regardless of counts.
        card_width = max(self.anilist_card.sizeHint().width(), self.mal_card.sizeHint().width())
        self.anilist_card.setMinimumWidth(card_width)
        self.mal_card.setMinimumWidth(card_width)
        stats_layout.addWidget(self.anilist_card, 0, 0)
        stats_layout.addWidget(self.mal_card, 0, 1)
        # A third, empty stretch column so the two cards keep a sensible
        # width on a wide window instead of growing to fill it.
        stats_layout.setColumnStretch(2, 1)
        layout.addLayout(stats_layout)

        layout.addWidget(self._build_status_group())

        layout.addStretch(1)

        self.reload()

    def _build_accounts_group(self) -> QGroupBox:
        group = QGroupBox("Accounts", self)
        row = QHBoxLayout(group)

        self.anilist_status_label = QLabel("AniList: checking...", self)
        self.anilist_status_label.setWordWrap(True)
        row.addWidget(self.anilist_status_label, 1)
        self.myanimelist_status_label = QLabel("MyAnimeList: checking...", self)
        self.myanimelist_status_label.setWordWrap(True)
        row.addWidget(self.myanimelist_status_label, 1)

        return group

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status", self)
        layout = QVBoxLayout(group)

        self.last_sync_label = QLabel("Last sync: checking...", group)
        layout.addWidget(self.last_sync_label)

        attention_row = QHBoxLayout()
        self.attention_badge = Pill("0", "success", group)
        attention_row.addWidget(self.attention_badge)
        self.needs_attention_label = QLabel("", group)
        attention_row.addWidget(self.needs_attention_label, 1)
        self.go_to_mapping_issues_button = QPushButton("Review", group)
        self.go_to_mapping_issues_button.clicked.connect(
            lambda: self.navigate_requested.emit("mapping_issues")
        )
        self.go_to_mapping_issues_button.setVisible(False)
        attention_row.addWidget(self.go_to_mapping_issues_button)
        layout.addLayout(attention_row)

        return group

    # -- refresh -----------------------------------------------------------

    def reload(self) -> None:
        """Called whenever this page becomes current, and after events
        elsewhere that could change what it shows (login/logout, a sync
        finishing, settings saved). The local reads are cheap and always
        refresh; the live AniList/MyAnimeList fetch is throttled -- see
        _MIN_AUTO_REFRESH_INTERVAL_SECONDS."""
        self._refresh_last_sync()
        self._refresh_needs_attention()
        self._fetch_stats(force=False)

    def _on_refresh_clicked(self) -> None:
        self._refresh_last_sync()
        self._refresh_needs_attention()
        self._fetch_stats(force=True)

    def _fetch_stats(self, *, force: bool) -> None:
        if self._thread is not None:
            return  # a fetch is already in flight
        if not force and (time.monotonic() - self._last_fetch_at) < _MIN_AUTO_REFRESH_INTERVAL_SECONDS:
            return  # fetched recently enough -- avoid hammering the real APIs on quick navigation

        self.refresh_button.setEnabled(False)
        self.anilist_status_label.setText("AniList: checking...")
        self.myanimelist_status_label.setText("MyAnimeList: checking...")

        self._thread, self._worker = run_in_thread(
            self,
            fetch_dashboard_stats,
            self._get_config(),
            on_finished=self._on_stats_finished,
            on_error=self._on_stats_error,
        )

    def _refresh_last_sync(self) -> None:
        entry = load_last_sync(self._get_config().resolved_sync_history_path)
        if entry is None:
            self.last_sync_label.setText("Last sync: never synced yet.")
            return
        parts = []
        for kind, counts in sorted(entry.per_kind.items()):
            parts.append(f"{kind}: {counts.get('updated', 0)} updated, {counts.get('skipped', 0)} skipped")
        summary = "; ".join(parts) if parts else "no changes"
        self.last_sync_label.setText(f"Last sync: {entry.finished_at} ({summary})")

    def _refresh_needs_attention(self) -> None:
        state = load_unmapped_state(self._get_config().resolved_unmapped_state_path)
        count = len(state.entries)
        self.attention_badge.set_text_and_kind(str(count), "success" if count == 0 else "warning")
        if count == 0:
            self.needs_attention_label.setText("Nothing needs your attention.")
            self.go_to_mapping_issues_button.setVisible(False)
        else:
            entry_word = "entry" if count == 1 else "entries"
            self.needs_attention_label.setText(f"{count} {entry_word} couldn't be matched automatically.")
            self.go_to_mapping_issues_button.setVisible(True)

    def _on_stats_finished(self, stats: object) -> None:
        assert isinstance(stats, DashboardStats)
        self._thread = None
        self._worker = None
        self._last_fetch_at = time.monotonic()
        self.refresh_button.setEnabled(True)
        self._render_platform_status("AniList", self.anilist_status_label, stats.anilist)
        self._render_platform_status("MyAnimeList", self.myanimelist_status_label, stats.myanimelist)
        self._render_stat_cards(stats)

    def _on_stats_error(self, message: str) -> None:
        self._thread = None
        self._worker = None
        self._last_fetch_at = time.monotonic()
        self.refresh_button.setEnabled(True)
        self.anilist_status_label.setText("AniList: couldn't check status.")
        self.myanimelist_status_label.setText("MyAnimeList: couldn't check status.")
        for card in (self.anilist_card, self.mal_card):
            card.set_value("Anime", "--")
            card.set_value("Manga", "--")
            card.set_subtext(f"Error: {message}", color=DANGER)

    def _render_platform_status(self, name: str, label: QLabel, status: PlatformStatus) -> None:
        if not status.authenticated:
            label.setText(f"{name}: not logged in.")
            label.setStyleSheet(f"color: {DANGER};")
        elif status.error:
            label.setText(f"{name}: logged in, but couldn't load data ({status.error}).")
            label.setStyleSheet(f"color: {WARNING};")
        else:
            label.setText(f"{name}: connected.")
            label.setStyleSheet(f"color: {SUCCESS};")

    def _render_stat_cards(self, stats: DashboardStats) -> None:
        self._render_one_stat_card(self.anilist_card, stats.anilist)
        self._render_one_stat_card(self.mal_card, stats.myanimelist)

    def _render_one_stat_card(self, card: StatCard, status: PlatformStatus) -> None:
        if status.anime_count is not None and status.manga_count is not None:
            card.set_value("Anime", status.anime_count)
            card.set_value("Manga", status.manga_count)
            card.set_subtext("")
            return
        card.set_value("Anime", "--")
        card.set_value("Manga", "--")
        if not status.authenticated:
            card.set_subtext("Log in to see this.")
        elif status.error:
            card.set_subtext(f"Error: {status.error}", color=DANGER)
