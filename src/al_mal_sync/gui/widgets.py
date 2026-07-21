"""Small reusable widgets/helpers shared across pages: a collapsible section
(used by Sync's "Advanced options" and Mapping Issues' "Manual overrides"),
a divider, a colored status pill, a source-breakdown bar, a stat card (used
by the Dashboard's tiles), and two layout helpers -- the design.md component
set adapted to Qt.

The layout helpers exist because Qt's QVBoxLayout/QFormLayout stretch every
child to the full width of their parent by default -- with no page-level
width cap, a QComboBox or QPushButton ends up spanning the *entire* window
width on anything wider than a laptop screen, which reads as broken/
unstyled rather than deliberate. cap_width()/left_aligned() are the fix,
applied at each call site rather than through a page-wide max-width wrapper,
since some pages (tables, log panels) genuinely want the full width and
others (forms, buttons) don't.

Pill is only a good fit for short, fixed-vocabulary text (a state word, a
count) -- an early version of this rework used it for full status
sentences on the Dashboard/Login pages, and the fixed chip padding made long
text visibly overflow its rounded background. Those pages use plain
colored QLabels instead; Pill is reserved for genuinely short badges (see
the Dashboard's "needs attention" count).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QToolButton, QVBoxLayout, QWidget

from .theme import ANILIST_COLOR, MYANIMELIST_COLOR

PAGE_MARGIN = 24
PAGE_SPACING = 16


def apply_page_layout(layout: QLayout) -> None:
    """Consistent outer margin/spacing for a page's top-level layout --
    applied once per page instead of each page picking its own inset."""
    layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    layout.setSpacing(PAGE_SPACING)


def cap_width(widget: QWidget, width: int) -> QWidget:
    """Stops `widget` (a combo box, line edit, ...) from stretching to fill
    its parent layout's full width. Returns the widget so this can wrap an
    addRow()/addWidget() call inline."""
    widget.setMaximumWidth(width)
    return widget


def left_aligned(widget: QWidget, width: int | None = None) -> QHBoxLayout:
    """Wraps `widget` (typically a QPushButton) in a row with a trailing
    stretch, so it reads as a normal-sized, left-aligned control instead of
    stretching to the full width of its parent QVBoxLayout."""
    if width is not None:
        widget.setMaximumWidth(width)
    row = QHBoxLayout()
    row.addWidget(widget)
    row.addStretch(1)
    return row


class CollapsibleSection(QWidget):
    """A header button that shows/hides a content widget. Collapsed by
    default -- meant for advanced/rarely-needed content that shouldn't
    compete with a page's primary controls."""

    def __init__(
        self, title: str, content: QWidget, *, collapsed: bool = True, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.toggle_button = QToolButton(self)
        self.toggle_button.setObjectName("collapsibleHeader")
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow
        )
        self.toggle_button.clicked.connect(self._on_toggled)
        layout.addWidget(self.toggle_button)

        self.content = content
        self.content.setVisible(not collapsed)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool) -> None:
        self.content.setVisible(checked)
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def set_collapsed(self, collapsed: bool) -> None:
        """Expands/collapses programmatically -- used when a page loads
        existing data that makes the advanced content directly relevant
        (e.g. Settings expanding the cron section when a cron schedule is
        already saved), not just in response to the header being clicked."""
        self.toggle_button.setChecked(not collapsed)
        self._on_toggled(not collapsed)


class Divider(QFrame):
    """A thin horizontal rule -- design.md's activity-feed row separator:
    "Cards separated by thin divider, no visible border/box." Used in place
    of a card border wherever rows in a list need visual separation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("divider")
        self.setFrameShape(QFrame.Shape.NoFrame)


class Pill(QLabel):
    """A small rounded-full colored status tag, e.g. "connected" / "not
    logged in" -- design.md's pills/badges component. `kind` selects the
    fill color via a Qt dynamic property that theme.py's QSS matches on
    (one of success/warning/danger/accent/neutral/anilist/myanimelist)."""

    def __init__(self, text: str = "", kind: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("pill", True)
        self._kind = kind
        self.setProperty("pillKind", kind)

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.setProperty("pillKind", kind)
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def set_text_and_kind(self, text: str, kind: str) -> None:
        self.setText(text)
        self.set_kind(kind)


class SourceBreakdownBar(QWidget):
    """Horizontal proportional bar showing an AniList-vs-MyAnimeList split
    of a count, with a legend row above -- design.md's Genre Overview bar,
    repurposed per design.md section 7 as a "source breakdown" instead of a
    genre breakdown. AniList's segment always renders in `ANILIST_COLOR`,
    MyAnimeList's in `MYANIMELIST_COLOR`, so the same two colors identify
    each platform everywhere both appear side by side."""

    _BAR_HEIGHT = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        legend = QHBoxLayout()
        self.anilist_label = QLabel(self)
        self.anilist_label.setStyleSheet(f"color: {ANILIST_COLOR}; font-weight: 600;")
        legend.addWidget(self.anilist_label)
        legend.addStretch(1)
        self.myanimelist_label = QLabel(self)
        self.myanimelist_label.setStyleSheet(f"color: {MYANIMELIST_COLOR}; font-weight: 600;")
        legend.addWidget(self.myanimelist_label)
        layout.addLayout(legend)

        bar = QWidget(self)
        bar.setFixedHeight(self._BAR_HEIGHT)
        self._bar_layout = QHBoxLayout(bar)
        self._bar_layout.setContentsMargins(0, 0, 0, 0)
        self._bar_layout.setSpacing(2)
        anilist_segment = QFrame(bar)
        anilist_segment.setStyleSheet(f"background: {ANILIST_COLOR}; border-radius: 4px;")
        mal_segment = QFrame(bar)
        mal_segment.setStyleSheet(f"background: {MYANIMELIST_COLOR}; border-radius: 4px;")
        self._bar_layout.addWidget(anilist_segment)
        self._bar_layout.addWidget(mal_segment)
        layout.addWidget(bar)

        self.set_counts(0, 0)

    def set_counts(self, anilist_count: int, mal_count: int) -> None:
        self.anilist_label.setText(f"AniList {anilist_count}")
        self.myanimelist_label.setText(f"MyAnimeList {mal_count}")
        anilist_share = max(anilist_count, 0)
        mal_share = max(mal_count, 0)
        if anilist_share == 0 and mal_share == 0:
            anilist_share, mal_share = 1, 1
        self._bar_layout.setStretch(0, anilist_share)
        self._bar_layout.setStretch(1, mal_share)


class StatCard(QFrame):
    """A small card: a muted title, a big value, an optional colored
    sub-label (e.g. a diff or status note), and an optional AniList/
    MyAnimeList source-breakdown bar underneath (see `set_breakdown`)."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        self.value_label = QLabel("--", self)
        self.value_label.setObjectName("statValue")
        layout.addWidget(self.value_label)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        layout.addWidget(self.subtext_label)

        self.breakdown_bar = SourceBreakdownBar(self)
        self.breakdown_bar.setVisible(False)
        layout.addWidget(self.breakdown_bar)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")

    def set_breakdown(self, anilist_count: int, mal_count: int) -> None:
        self.breakdown_bar.set_counts(anilist_count, mal_count)
        self.breakdown_bar.setVisible(True)

    def hide_breakdown(self) -> None:
        self.breakdown_bar.setVisible(False)
