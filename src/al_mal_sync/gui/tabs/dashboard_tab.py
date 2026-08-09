"""Dashboard page: the first thing a user sees. A quick-glance summary --
how big each platform's library is, whether both accounts are connected,
and whether anything needs attention -- with a "Review" jump straight to
Mapping Issues, since that one's conditional (only appears when there's
actually something to act on). No AniList/MyAnimeList numeric IDs anywhere
here; those only matter on the Mapping Issues page where they're actually
actionable.

Accounts is a card (AccountStatusCard in widgets.py), not the separate
plain-text group box an earlier version used -- a PlatformBadge monogram +
colored StatusIcon per platform reads at a glance the way "AniList:
connected." text doesn't, and it sits as the first card in the same
Library-size row rather than its own section, since it's the same
"one glance per platform" shape as those cards. The full status sentence
(including any fetch-error text) isn't gone, just demoted to the icon's
tooltip.

Deliberately has no "Go to Sync"/"Go to Login" buttons -- those pages are
already one click away in the always-visible sidebar, so a second button here
pointing at the same place was pure redundancy, not a shortcut.

Below that, a "Library Stats" section with a source selector: AniList's list
data supports strictly more stats than MAL's (a normalizable score scale, a
per-episode duration to estimate watch time -- see stats.py), so switching
the selector to MyAnimeList Stats hides the widgets that need data MAL simply
doesn't provide, rather than showing them empty or wrong. Both platforms'
LibraryStats are already sitting on the last fetched DashboardStats, so
switching sources is a pure re-render -- no extra network call.

Anime and manga get their own side-by-side column each (status donut,
overview, score histogram, top genres as bars, top genres again as a donut),
never mixed in the same widget -- an earlier version put "episodes watched"
and "chapters/volumes read" in one shared Progress card, which read as
arbitrary and made the anime/manga split hard to scan at a glance. Modeled
after AniList's own stats page (icons, donut/column charts, not just
label:value text), scaled down to what fits one page: DonutChart for the
status breakdown, ScoreDistributionCard for the score histogram,
GenreBreakdownCard's bars *and* GenreDonutCard's pie slices for top genres
(same ranked data, two shapes -- AniList's own Format/Status/Country
sections are exactly this kind of small-pie-chart treatment), and
IconBadge-adorned StatCard rows for the Overview/Library-size numbers (all
in widgets.py). The whole page is wrapped in a QScrollArea so this doesn't
just get cut off on a window shorter than the content.

Counts and stats are fetched live (dashboard.fetch_dashboard_stats) on a
worker thread, same run_in_thread pattern as every other page's network
calls. The unmapped count and last-sync summary are cheap local file reads,
done synchronously.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...config import Config
from ...dashboard import DashboardStats, PlatformStatus, fetch_dashboard_stats
from ...sync_history import load_last_sync
from ...unmapped import load_unmapped_state
from ..theme import DANGER
from ..widgets import (
    AccountStatusCard,
    GenreBreakdownCard,
    GenreDonutCard,
    Pill,
    ScoreDistributionCard,
    StatCard,
    StatusBreakdownCard,
    apply_page_layout,
)
from ..workers import run_in_thread

# (bucket_key, display_label, pillKind) -- shared between the anime/manga
# StatusBreakdownCards, since the underlying buckets are identical (stats.py
# maps both platforms' own status vocabularies onto the same five). Only the
# "current" row's label differs (Watching vs Reading), passed in separately.
_STATUS_KINDS = [
    ("current", "accent"),
    ("completed", "success"),
    ("planning", "neutral"),
    ("paused", "warning"),
    ("dropped", "danger"),
]


def _status_segments(current_label: str) -> list[tuple[str, str, str]]:
    labels = {
        "current": current_label,
        "completed": "Completed",
        "planning": "Planning",
        "paused": "Paused",
        "dropped": "Dropped",
    }
    return [(key, labels[key], kind) for key, kind in _STATUS_KINDS]


def _format_score(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"

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
        self._last_dashboard_stats: DashboardStats | None = None

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea(self)
        scroll_area.setObjectName("pageScrollArea")
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)
        outer_layout.addWidget(scroll_area)

        content = QWidget(scroll_area)
        scroll_area.setWidget(content)
        layout = QVBoxLayout(content)
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

        stats_layout = QGridLayout()
        stats_layout.setSpacing(16)
        self.accounts_card = AccountStatusCard(self)
        library_size_icons = {"Anime": "tv", "Manga": "book"}
        self.anilist_card = StatCard(
            "AniList Library size", ["Anime", "Manga"], self, icons=library_size_icons
        )
        self.mal_card = StatCard(
            "MyAnimeList Library size", ["Anime", "Manga"], self, icons=library_size_icons
        )
        # StatCard's own fixed-width value column keeps the two cards equal
        # width when it's the *counts* that differ, but "MyAnimeList Library
        # size" is simply longer text than "AniList Library size" -- without
        # normalizing to the wider card's natural width here too, the titles
        # alone would make the cards visibly mismatched regardless of counts.
        # The Accounts card's content is different in shape (two short rows,
        # not a title + big number), so it's left out of this normalization
        # and just takes its own natural width.
        card_width = max(self.anilist_card.sizeHint().width(), self.mal_card.sizeHint().width())
        self.anilist_card.setMinimumWidth(card_width)
        self.mal_card.setMinimumWidth(card_width)
        stats_layout.addWidget(self.accounts_card, 0, 0)
        stats_layout.addWidget(self.anilist_card, 0, 1)
        stats_layout.addWidget(self.mal_card, 0, 2)
        # A fourth, empty stretch column so the cards keep a sensible width
        # on a wide window instead of growing to fill it.
        stats_layout.setColumnStretch(3, 1)
        layout.addLayout(stats_layout)

        layout.addWidget(self._build_library_stats_group())

        layout.addWidget(self._build_status_group())

        layout.addStretch(1)

        self.reload()

    def _build_library_stats_group(self) -> QGroupBox:
        group = QGroupBox("Library Stats", self)
        layout = QVBoxLayout(group)

        header = QHBoxLayout()
        header.addWidget(QLabel("Source:", group))
        self.stats_source_combo = QComboBox(group)
        self.stats_source_combo.addItem("AniList Stats", "anilist")
        self.stats_source_combo.addItem("MyAnimeList Stats", "myanimelist")
        self.stats_source_combo.currentIndexChanged.connect(lambda _index: self._render_library_stats())
        header.addWidget(self.stats_source_combo)
        header.addStretch(1)
        layout.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        columns.addLayout(self._build_anime_stats_column(group))
        columns.addLayout(self._build_manga_stats_column(group))
        layout.addLayout(columns)

        self._library_stat_widgets = [
            self.anime_status_card,
            self.anime_stats_card,
            self.anime_score_card,
            self.anime_genre_card,
            self.anime_genre_donut_card,
            self.manga_status_card,
            self.manga_stats_card,
            self.manga_score_card,
            self.manga_genre_card,
            self.manga_genre_donut_card,
        ]
        # See theme.py's QFrame#card[compact="true"] rule -- these cards sit
        # two-per-column here, and don't need the 28px title-clearance margin
        # a lone StatCard normally gets under a page-level heading.
        for widget in self._library_stat_widgets:
            widget.setProperty("compact", True)

        return group

    def _build_anime_stats_column(self, parent: QWidget) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(12)
        heading = QLabel("Anime", parent)
        heading.setObjectName("sectionHeading")
        column.addWidget(heading)

        self.anime_status_card = StatusBreakdownCard(
            "Status", _status_segments("Watching"), parent
        )
        column.addWidget(self.anime_status_card)
        # Days Watched is AniList-only -- MAL's my_list_status has no
        # per-episode duration to estimate watch time from, so this row is
        # hidden entirely rather than shown as a permanent "--" (see
        # _render_library_stats).
        self.anime_stats_card = StatCard(
            "Overview",
            ["Mean Score", "Episodes Watched", "Days Watched"],
            parent,
            icons={"Mean Score": "star", "Episodes Watched": "play", "Days Watched": "clock"},
        )
        column.addWidget(self.anime_stats_card)
        self.anime_score_card = ScoreDistributionCard("Score Distribution", parent)
        column.addWidget(self.anime_score_card)
        self.anime_genre_card = GenreBreakdownCard("Top Genres", parent)
        column.addWidget(self.anime_genre_card)
        self.anime_genre_donut_card = GenreDonutCard("Genre Distribution", parent)
        column.addWidget(self.anime_genre_donut_card)
        column.addStretch(1)
        return column

    def _build_manga_stats_column(self, parent: QWidget) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(12)
        heading = QLabel("Manga", parent)
        heading.setObjectName("sectionHeading")
        column.addWidget(heading)

        self.manga_status_card = StatusBreakdownCard(
            "Status", _status_segments("Reading"), parent
        )
        column.addWidget(self.manga_status_card)
        self.manga_stats_card = StatCard(
            "Overview",
            ["Mean Score", "Chapters Read", "Volumes Read"],
            parent,
            icons={"Mean Score": "star", "Chapters Read": "play", "Volumes Read": "book"},
        )
        column.addWidget(self.manga_stats_card)
        self.manga_score_card = ScoreDistributionCard("Score Distribution", parent)
        column.addWidget(self.manga_score_card)
        self.manga_genre_card = GenreBreakdownCard("Top Genres", parent)
        column.addWidget(self.manga_genre_card)
        self.manga_genre_donut_card = GenreDonutCard("Genre Distribution", parent)
        column.addWidget(self.manga_genre_donut_card)
        column.addStretch(1)
        return column

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
        self.accounts_card.set_checking()

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
        self._last_dashboard_stats = stats
        self.accounts_card.set_platform_status(
            "anilist", "AniList", authenticated=stats.anilist.authenticated, error=stats.anilist.error
        )
        self.accounts_card.set_platform_status(
            "myanimelist",
            "MyAnimeList",
            authenticated=stats.myanimelist.authenticated,
            error=stats.myanimelist.error,
        )
        self._render_stat_cards(stats)
        self._render_library_stats()

    def _on_stats_error(self, message: str) -> None:
        self._thread = None
        self._worker = None
        self._last_fetch_at = time.monotonic()
        self.refresh_button.setEnabled(True)
        self._last_dashboard_stats = None
        self.accounts_card.set_error("couldn't check status")
        for card in (self.anilist_card, self.mal_card):
            card.set_value("Anime", "--")
            card.set_value("Manga", "--")
            card.set_subtext(f"Error: {message}", color=DANGER)
        for widget in self._library_stat_widgets:
            widget.clear_values()
            widget.set_subtext(f"Error: {message}", color=DANGER)

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

    # -- library stats (per-source, no separate fetch) ----------------------

    def _render_library_stats(self) -> None:
        source = self.stats_source_combo.currentData()
        # AniList-only row -- see the Overview-card comment in
        # _build_anime_stats_column. Toggled on every selector change even if
        # we have no data yet, so it's never briefly visible under MAL.
        self.anime_stats_card.set_row_visible("Days Watched", source == "anilist")

        if self._last_dashboard_stats is None:
            return
        status = (
            self._last_dashboard_stats.anilist
            if source == "anilist"
            else self._last_dashboard_stats.myanimelist
        )
        self._render_library_stat_widgets(status)

    def _render_library_stat_widgets(self, status: PlatformStatus) -> None:
        stats = status.stats
        if stats is None:
            subtext = "Log in to see this." if not status.authenticated else ""
            if status.error:
                subtext = f"Error: {status.error}"
            for widget in self._library_stat_widgets:
                widget.clear_values()
                widget.set_subtext(subtext, color=DANGER if status.error else None)
            return

        for widget in self._library_stat_widgets:
            widget.set_subtext("")

        self.anime_status_card.set_counts(
            {
                "current": stats.anime_status.current,
                "completed": stats.anime_status.completed,
                "planning": stats.anime_status.planning,
                "paused": stats.anime_status.paused,
                "dropped": stats.anime_status.dropped,
            }
        )
        self.manga_status_card.set_counts(
            {
                "current": stats.manga_status.current,
                "completed": stats.manga_status.completed,
                "planning": stats.manga_status.planning,
                "paused": stats.manga_status.paused,
                "dropped": stats.manga_status.dropped,
            }
        )

        self.anime_stats_card.set_value("Mean Score", _format_score(stats.anime_mean_score))
        self.anime_stats_card.set_value("Episodes Watched", stats.anime_episodes_watched)
        if stats.anime_days_watched is not None:
            self.anime_stats_card.set_value("Days Watched", f"{stats.anime_days_watched:.1f}")
        else:
            self.anime_stats_card.set_value("Days Watched", "--")

        self.manga_stats_card.set_value("Mean Score", _format_score(stats.manga_mean_score))
        self.manga_stats_card.set_value("Chapters Read", stats.manga_chapters_read)
        self.manga_stats_card.set_value("Volumes Read", stats.manga_volumes_read)

        self.anime_score_card.set_distribution(stats.anime_score_distribution)
        self.manga_score_card.set_distribution(stats.manga_score_distribution)

        self.anime_genre_card.set_counts(stats.anime_genre_counts)
        self.manga_genre_card.set_counts(stats.manga_genre_counts)

        self.anime_genre_donut_card.set_counts(stats.anime_genre_counts)
        self.manga_genre_donut_card.set_counts(stats.manga_genre_counts)
