"""Small reusable widgets/helpers shared across pages: a collapsible section
(used by Sync's "Advanced options" and Mapping Issues' "Manual overrides"),
a divider, a colored status pill, a stat card (used by the Dashboard's
tiles), and two layout helpers -- the design.md component set adapted to
Qt.

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

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QToolButton, QVBoxLayout, QWidget

from .theme import (
    ACCENT,
    ACCENT_SOFT,
    ANILIST_COLOR,
    DANGER,
    MYANIMELIST_COLOR,
    PAGE_BG,
    SUCCESS,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING,
)

PAGE_MARGIN = 24
PAGE_SPACING = 16

# Chart-painting color lookup, sharing the same pillKind vocabulary as the
# QSS-driven Pill/GenreBreakdownCard tokens (theme.py) -- charts painted
# directly with QPainter can't pick up a QSS dynamic-property rule, so they
# need the actual QColor-able hex values instead. "neutral" reuses
# TEXT_SECONDARY rather than SURFACE_ALT for the same reason theme.py's own
# QFrame#statusBarSegment[pillKind="neutral"] override does: a chart segment
# that's the near-invisible generic pill color reads as a gap, not a slice.
_CHART_COLORS = {
    "accent": ACCENT,
    "success": SUCCESS,
    "warning": WARNING,
    "danger": DANGER,
    "neutral": TEXT_SECONDARY,
}


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


def field_and_button_row(field: QWidget, button: QWidget, total_width: int) -> QHBoxLayout:
    """A field (e.g. a read-only path QLineEdit) plus a trailing button,
    sized so their combined width exactly matches `total_width` -- keeps the
    button's right edge aligned with cap_width()-ed fields above/below it in
    the same form, instead of the button overhanging past them."""
    row = QHBoxLayout()
    row.setSpacing(6)
    field.setFixedWidth(total_width - button.sizeHint().width() - row.spacing())
    row.addWidget(field)
    row.addWidget(button)
    # Without this, the button (not the field) is what QHBoxLayout stretches
    # to fill the form field column's leftover width, since the field is now
    # fixed-width -- same reasoning as left_aligned()'s trailing stretch.
    row.addStretch(1)
    return row


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


class IconBadge(QWidget):
    """A small rounded-square badge with a minimal vector glyph, painted
    directly with QPainter -- no icon-font or SVG asset dependency, just a
    handful of primitive shapes per `kind`. Pairs a StatCard row's number
    with a pictogram the way AniList's own stats page does (a monitor icon
    next to "Total Anime", a play triangle next to "Episodes Watched", ...)
    instead of leaving every row as bare label:value text."""

    _SIZE = 32

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(self._SIZE, self._SIZE)

    def paintEvent(self, event) -> None:  # noqa: ARG002 -- Qt override signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bounds = QRectF(0, 0, self._SIZE, self._SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT_SOFT))
        painter.drawRoundedRect(bounds, 8, 8)

        glyph_rect = bounds.adjusted(8, 8, -8, -8)
        draw = getattr(self, f"_draw_{self._kind}", None)
        if draw is not None:
            draw(painter, glyph_rect)
        painter.end()

    def _draw_tv(self, painter: QPainter, r: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        screen = QRectF(r.left(), r.top(), r.width(), r.height() * 0.72)
        painter.drawRoundedRect(screen, 1.5, 1.5)
        stand_width = r.width() * 0.5
        stand = QRectF(r.center().x() - stand_width / 2, screen.bottom() + 2, stand_width, r.height() * 0.12)
        painter.drawRect(stand)

    def _draw_book(self, painter: QPainter, r: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        page_width = r.width() * 0.46
        left = QRectF(r.left(), r.top(), page_width, r.height())
        right = QRectF(r.right() - page_width, r.top(), page_width, r.height())
        painter.drawRoundedRect(left, 1.5, 1.5)
        painter.drawRoundedRect(right, 1.5, 1.5)

    def _draw_play(self, painter: QPainter, r: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        triangle = QPolygonF([r.topLeft(), QPointF(r.left(), r.bottom()), QPointF(r.right(), r.center().y())])
        painter.drawPolygon(triangle)

    def _draw_clock(self, painter: QPainter, r: QRectF) -> None:
        pen = QPen(QColor(ACCENT), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(r)
        center = r.center()
        painter.drawLine(center, QPointF(center.x(), r.top() + r.height() * 0.15))
        painter.drawLine(center, QPointF(r.right() - r.width() * 0.22, center.y()))

    def _draw_star(self, painter: QPainter, r: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        center = r.center()
        outer = min(r.width(), r.height()) / 2
        inner = outer * 0.45
        points = []
        for i in range(10):
            angle = math.pi / 2 + i * math.pi / 5
            radius = outer if i % 2 == 0 else inner
            points.append(QPointF(center.x() + radius * math.cos(angle), center.y() - radius * math.sin(angle)))
        painter.drawPolygon(QPolygonF(points))


class PlatformBadge(QWidget):
    """A small colored-circle monogram for a platform (AniList/MyAnimeList),
    reusing the same "colored circle + bold initials" approach as the app's
    own window icon (main_window.py's _build_app_icon) rather than trying to
    reproduce either site's real logo. Uses the brand-color pair (theme.py's
    ANILIST_COLOR/MYANIMELIST_COLOR) already used everywhere else two
    platforms' numbers sit side by side."""

    _SIZE = 28
    _LABELS = {"anilist": "AL", "myanimelist": "M"}
    _COLORS = {"anilist": ANILIST_COLOR, "myanimelist": MYANIMELIST_COLOR}
    # ANILIST_COLOR is a bright, light blue -- dark text reads best on it
    # (matches theme.py's QLabel[pillKind="accent"] override). MYANIMELIST_COLOR
    # is a medium purple, closer to SUCCESS/neutral pills, which keep the
    # default light text rather than overriding it -- same choice here.
    _TEXT_COLORS = {"anilist": PAGE_BG, "myanimelist": TEXT_PRIMARY}

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setFixedSize(self._SIZE, self._SIZE)

    def paintEvent(self, event) -> None:  # noqa: ARG002 -- Qt override signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(0, 0, self._SIZE, self._SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._COLORS[self._kind]))
        painter.drawEllipse(bounds)

        painter.setPen(QColor(self._TEXT_COLORS[self._kind]))
        font = QFont(painter.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, self._LABELS[self._kind])
        painter.end()


class StatusIcon(QWidget):
    """A small colored circle with a checkmark/exclamation/cross glyph,
    painted with QPainter -- the Dashboard's Accounts card uses this instead
    of a "connected."/"not logged in." sentence, so the state reads at a
    glance from color+shape alone. The full descriptive text (including any
    fetch-error message) isn't lost, just demoted to this widget's tooltip
    (see AccountStatusCard.set_platform_status)."""

    _SIZE = 20
    _COLORS = {"success": SUCCESS, "warning": WARNING, "danger": DANGER, "checking": TEXT_SECONDARY}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "checking"
        self.setFixedSize(self._SIZE, self._SIZE)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002 -- Qt override signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(0, 0, self._SIZE, self._SIZE)
        color = QColor(self._COLORS.get(self._state, DANGER))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(bounds)

        # "checking" (fetch in flight, state not known yet) is a plain dot
        # -- no glyph -- so it can't be mistaken for a real success/warning/
        # danger reading before the fetch actually completes.
        if self._state == "checking":
            painter.end()
            return

        pen = QPen(QColor(PAGE_BG), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        r = bounds.adjusted(5, 5, -5, -5)
        if self._state == "success":
            painter.drawLine(
                QPointF(r.left(), r.top() + r.height() * 0.55),
                QPointF(r.left() + r.width() * 0.4, r.bottom()),
            )
            painter.drawLine(QPointF(r.left() + r.width() * 0.4, r.bottom()), r.topRight())
        elif self._state == "warning":
            painter.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom() - 3))
            dot = QPointF(r.center().x(), r.bottom())
            painter.drawLine(dot, dot)
        else:
            painter.drawLine(r.topLeft(), r.bottomRight())
            painter.drawLine(r.topRight(), r.bottomLeft())
        painter.end()


class AccountStatusCard(QFrame):
    """Replaces the old plain-text "Accounts" group box: pairs each
    platform's PlatformBadge monogram with a colored StatusIcon instead of
    a "connected."/"not logged in." sentence sitting in its own separate
    section. Lives as the first card in the Dashboard's Library size row
    instead, since it's the same "one glance per platform" shape as those
    cards."""

    _PLATFORMS = [("anilist", "AniList"), ("myanimelist", "MyAnimeList")]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.title_label = QLabel("Accounts", self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        self._status_icons: dict[str, StatusIcon] = {}
        for key, label in self._PLATFORMS:
            row_widget = QWidget(self)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            row.addWidget(PlatformBadge(key, row_widget))
            name_label = QLabel(label, row_widget)
            row.addWidget(name_label)
            row.addStretch(1)
            status_icon = StatusIcon(row_widget)
            row.addWidget(status_icon)
            layout.addWidget(row_widget)
            self._status_icons[key] = status_icon

    def set_platform_status(self, key: str, label: str, *, authenticated: bool, error: str | None) -> None:
        icon = self._status_icons[key]
        if not authenticated:
            icon.set_state("danger")
            icon.setToolTip(f"{label}: not logged in.")
        elif error:
            icon.set_state("warning")
            icon.setToolTip(f"{label}: logged in, but couldn't load data ({error}).")
        else:
            icon.set_state("success")
            icon.setToolTip(f"{label}: connected.")

    def set_checking(self) -> None:
        """Called right before a fetch starts -- resets both platforms back
        to the neutral "checking" dot so a stale success/danger reading from
        the *previous* fetch never lingers through the new one."""
        for key, label in self._PLATFORMS:
            icon = self._status_icons[key]
            icon.set_state("checking")
            icon.setToolTip(f"{label}: checking...")

    def set_error(self, message: str) -> None:
        """Both platforms failed for a reason unrelated to login state (the
        fetch itself raised) -- distinct from set_platform_status's
        "not logged in" danger reading, which would otherwise be misleading
        here."""
        for key, label in self._PLATFORMS:
            icon = self._status_icons[key]
            icon.set_state("danger")
            icon.setToolTip(f"{label}: {message}.")


class StatCard(QFrame):
    """A small card: a muted title plus one labeled count row per entry in
    `row_labels` (e.g. "Anime" / "Manga"), and an optional error subtext
    line underneath (see `set_subtext`).

    Each row's value sits in a fixed-minimum-width column sized to fit
    `_VALUE_DIGITS` digits -- without it, two sibling cards (one per
    platform) visibly differ in size purely because one platform's counts
    happen to have more digits than the other's, which reads as a layout
    bug rather than "these are just two different numbers". Counts within
    that digit budget all render at the same card width; a card only grows
    past it once a count genuinely needs the room (a 5-digit library)."""

    _VALUE_DIGITS = 4

    def __init__(
        self,
        title: str,
        row_labels: list[str],
        parent: QWidget | None = None,
        *,
        icons: dict[str, str] | None = None,
    ) -> None:
        """`icons`, if given, maps a subset of `row_labels` to an IconBadge
        `kind` (see IconBadge) -- rows not in the mapping get no icon."""
        super().__init__(parent)
        self.setObjectName("card")
        icons = icons or {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        self._value_labels: dict[str, QLabel] = {}
        self._row_widgets: dict[str, QWidget] = {}
        min_value_width = 0
        for row_label in row_labels:
            row_widget = QWidget(self)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            if row_label in icons:
                row.addWidget(IconBadge(icons[row_label], row_widget))
            label = QLabel(row_label, self)
            label.setObjectName("muted")
            row.addWidget(label)
            row.addStretch(1)

            value = QLabel("--", self)
            value.setObjectName("statValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # ensurePolished() forces the "statValue" QSS rule (font-size:
            # 26px, weight 700) to actually apply before fontMetrics() reads
            # it -- without this, measurement happens against the
            # not-yet-styled default font and undershoots the real width.
            value.ensurePolished()
            if not min_value_width:
                min_value_width = value.fontMetrics().horizontalAdvance("9" * self._VALUE_DIGITS)
            value.setMinimumWidth(min_value_width)
            row.addWidget(value)

            layout.addWidget(row_widget)
            self._value_labels[row_label] = value
            self._row_widgets[row_label] = row_widget

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

    def set_value(self, row_label: str, value: int | str) -> None:
        self._value_labels[row_label].setText(str(value))

    def set_row_visible(self, row_label: str, visible: bool) -> None:
        """Hides a row entirely (not just blanking its value) -- for a stat
        that only one of several data sources this card can display for can
        actually supply (e.g. Days Watched, AniList-only -- see
        dashboard_tab.py)."""
        self._row_widgets[row_label].setVisible(visible)

    def clear_values(self) -> None:
        """Resets every row back to the "--" placeholder -- used when the
        underlying data becomes unavailable (logged out, fetch error, or a
        widget that only applies to a different data source) instead of
        leaving stale numbers on screen."""
        for row_label in self._value_labels:
            self.set_value(row_label, "--")

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))


class GenreBreakdownCard(QFrame):
    """A card ranking a library's most-common genres: one row per genre in
    the top N (highest count first, so row 1 doubles as the "favourite
    genre"), each with a bar sized relative to the top genre's count and its
    exact entry count alongside. Unlike StatusBreakdownCard, the row set
    isn't fixed at construction -- which genres appear, and how many rows
    there are, depends on the library's actual data, so set_counts rebuilds
    the row widgets each call instead of just updating values in place."""

    _BAR_WIDTH = 90
    _BAR_HEIGHT = 8

    def __init__(self, title: str, parent: QWidget | None = None, *, limit: int = 5) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._limit = limit

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)
        layout.addLayout(self._rows_layout)

        self._placeholder_label = QLabel("--", self)
        self._placeholder_label.setObjectName("muted")
        layout.addWidget(self._placeholder_label)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

        self.clear_values()

    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_counts(self, counts: dict[str, int]) -> None:
        self._clear_rows()
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: self._limit]
        self._placeholder_label.setVisible(not top)
        if not top:
            self.set_subtext("")
            return

        max_count = top[0][1]
        for name, count in top:
            # Rows are wrapped in a container QWidget (not just a bare
            # QHBoxLayout) so _clear_rows()'s item.widget() actually finds
            # something to deleteLater() next time around -- a bare
            # addLayout() row's child labels/frames are still parented
            # directly to `self` and survive takeAt() as invisible-but-alive
            # orphans, which showed up as stale genre bars bleeding through
            # after switching the Dashboard's stats source.
            row_widget = QWidget(self)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            name_label = QLabel(name, row_widget)
            row.addWidget(name_label, 1)

            bar = QWidget(row_widget)
            bar.setFixedSize(self._BAR_WIDTH, self._BAR_HEIGHT)
            bar_layout = QHBoxLayout(bar)
            bar_layout.setContentsMargins(0, 0, 0, 0)
            bar_layout.setSpacing(0)
            fill = QFrame(bar)
            fill.setObjectName("statusBarSegment")
            fill.setProperty("pillKind", "accent")
            bar_layout.addWidget(fill, count)
            if count < max_count:
                track = QFrame(bar)
                track.setObjectName("statusBarSegment")
                track.setProperty("pillKind", "neutral")
                bar_layout.addWidget(track, max_count - count)
            row.addWidget(bar)

            count_label = QLabel(str(count), row_widget)
            count_label.setObjectName("legendValue")
            count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(count_label)

            self._rows_layout.addWidget(row_widget)
        self.set_subtext("")

    def clear_values(self) -> None:
        self.set_counts({})

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))


class DonutChart(QWidget):
    """A ring chart painted directly with QPainter (no QtCharts dependency)
    -- one arc per non-zero segment, sized proportionally to its share of
    the total, plus the total printed in the hollow center. Replaces an
    earlier flat StatusDistributionBar: AniList's own stats page uses actual
    pie/donut charts for exactly this kind of share-of-whole data ("cake
    charts", per the user's own description), and a dashboard that's
    otherwise all bars and text benefits from the shape variety. Segment
    colors reuse the same `pillKind` token vocabulary as Pill, resolved
    through _CHART_COLORS since a QPainter pen needs a real QColor rather
    than a QSS dynamic-property selector."""

    _SIZE = 96
    _THICKNESS = 15

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[str, int]] = []
        self.setFixedSize(self._SIZE, self._SIZE)

    def set_counts(self, segments: list[tuple[str, int]]) -> None:
        """`segments`: (pillKind, count) pairs in display order."""
        self._segments = segments
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002 -- Qt override signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        inset = self._THICKNESS / 2
        ring_rect = QRectF(inset, inset, self._SIZE - 2 * inset, self._SIZE - 2 * inset)
        total = sum(count for _kind, count in self._segments)

        pen = QPen()
        pen.setWidthF(self._THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        if total <= 0:
            pen.setColor(QColor(_CHART_COLORS["neutral"]))
            painter.setPen(pen)
            painter.drawArc(ring_rect, 0, 360 * 16)
        else:
            # Qt angles are in 1/16ths of a degree, counter-clockwise from
            # 3 o'clock; starting at 90 (12 o'clock) and subtracting each
            # segment's span walks the ring clockwise, matching the legend's
            # top-to-bottom reading order.
            start_angle = 90 * 16
            for kind, count in self._segments:
                if count <= 0:
                    continue
                span = round(count / total * 360 * 16)
                pen.setColor(QColor(_CHART_COLORS.get(kind, _CHART_COLORS["neutral"])))
                painter.setPen(pen)
                painter.drawArc(ring_rect, start_angle, -span)
                start_angle -= span

        painter.setPen(QColor(TEXT_PRIMARY))
        font = QFont(painter.font())
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        center_text = str(total) if total > 0 else "--"
        painter.drawText(QRectF(0, 0, self._SIZE, self._SIZE), Qt.AlignmentFlag.AlignCenter, center_text)
        painter.end()


class StatusBreakdownCard(QFrame):
    """A card pairing a DonutChart with a legend of exact counts -- the
    Dashboard's Anime/Manga Status widgets. `segments` fixes the bucket
    order/labels/colors once at construction (the same status bucket reads
    as "Watching" for anime and "Reading" for manga, e.g.); set_counts just
    supplies the numbers per refresh."""

    def __init__(
        self, title: str, segments: list[tuple[str, str, str]], parent: QWidget | None = None
    ) -> None:
        """`segments`: (bucket_key, display_label, pillKind) tuples."""
        super().__init__(parent)
        self.setObjectName("card")
        self._segments = segments

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.chart = DonutChart(self)
        row.addWidget(self.chart)

        legend = QVBoxLayout()
        legend.setSpacing(6)
        self._legend_value_labels: dict[str, QLabel] = {}
        for bucket_key, display_label, kind in segments:
            legend_row = QHBoxLayout()
            legend_row.setSpacing(8)
            dot = QFrame(self)
            dot.setObjectName("legendDot")
            dot.setProperty("pillKind", kind)
            dot.setFixedSize(8, 8)
            legend_row.addWidget(dot)
            label = QLabel(display_label, self)
            label.setObjectName("muted")
            legend_row.addWidget(label)
            legend_row.addStretch(1)
            value = QLabel("--", self)
            value.setObjectName("legendValue")
            legend_row.addWidget(value)
            legend.addLayout(legend_row)
            self._legend_value_labels[bucket_key] = value
        row.addLayout(legend, 1)
        layout.addLayout(row)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

    def set_counts(self, counts: dict[str, int]) -> None:
        self.chart.set_counts([(kind, counts.get(key, 0)) for key, _label, kind in self._segments])
        for bucket_key, _label, _kind in self._segments:
            self._legend_value_labels[bucket_key].setText(str(counts.get(bucket_key, 0)))
        self.set_subtext("")

    def clear_values(self) -> None:
        self.chart.set_counts([])
        for value in self._legend_value_labels.values():
            value.setText("--")

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))


class GenreDonutCard(QFrame):
    """A second view of the same top-N genre ranking GenreBreakdownCard
    computes, as a DonutChart + legend instead of ranked bars -- mirrors
    StatusBreakdownCard's chart style (AniList's own Format/Status/Country
    sections are exactly this: several small pie charts, not just bars).
    Sits below the Top Genres bar list as a second, differently-shaped read
    of the same numbers rather than duplicating GenreBreakdownCard's layout
    logic wholesale.

    Unlike StatusBreakdownCard, the legend's bucket set isn't fixed at
    construction (which genres appear depends on the library's actual
    data), so it's rebuilt each set_counts call the same way
    GenreBreakdownCard's bar rows are -- including wrapping each legend row
    in its own QWidget so the cleanup loop can actually deleteLater() it
    (see GenreBreakdownCard's _clear_rows comment for why a bare
    addLayout() row doesn't work here)."""

    # Only 5 colors in the shared chart palette -- caps how many genres this
    # can show as distinct slices, same as GenreBreakdownCard's default limit.
    _SEGMENT_KINDS = ["accent", "success", "warning", "danger", "neutral"]

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._limit = len(self._SEGMENT_KINDS)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        row = QHBoxLayout()
        row.setSpacing(16)
        self.chart = DonutChart(self)
        row.addWidget(self.chart)

        self._legend_layout = QVBoxLayout()
        self._legend_layout.setSpacing(6)
        row.addLayout(self._legend_layout, 1)
        layout.addLayout(row)

        self._placeholder_label = QLabel("--", self)
        self._placeholder_label.setObjectName("muted")
        layout.addWidget(self._placeholder_label)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

        self.clear_values()

    def _clear_legend(self) -> None:
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_counts(self, counts: dict[str, int]) -> None:
        self._clear_legend()
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[: self._limit]
        has_data = bool(top)
        self.chart.setVisible(has_data)
        self._placeholder_label.setVisible(not has_data)
        if not has_data:
            self.chart.set_counts([])
            self.set_subtext("")
            return

        segments: list[tuple[str, int]] = []
        for (name, count), kind in zip(top, self._SEGMENT_KINDS):
            segments.append((kind, count))
            row_widget = QWidget(self)
            legend_row = QHBoxLayout(row_widget)
            legend_row.setContentsMargins(0, 0, 0, 0)
            legend_row.setSpacing(8)
            dot = QFrame(row_widget)
            dot.setObjectName("legendDot")
            dot.setProperty("pillKind", kind)
            dot.setFixedSize(8, 8)
            legend_row.addWidget(dot)
            label = QLabel(name, row_widget)
            label.setObjectName("muted")
            legend_row.addWidget(label)
            legend_row.addStretch(1)
            value = QLabel(str(count), row_widget)
            value.setObjectName("legendValue")
            legend_row.addWidget(value)
            self._legend_layout.addWidget(row_widget)

        self.chart.set_counts(segments)
        self.set_subtext("")

    def clear_values(self) -> None:
        self.set_counts({})

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))


class ColumnChart(QWidget):
    """A vertical bar/column histogram, painted directly with QPainter --
    AniList's own "Score" chart is exactly this shape (one column per score
    bucket, value printed above the bar, category label below it), and it
    needs a real baseline + per-column height rather than the proportional
    *widths* GenreBreakdownCard's layout-stretch bars use, so it's custom
    painting rather than nested QHBoxLayouts."""

    _CHART_HEIGHT = 90
    _VALUE_LABEL_HEIGHT = 16
    _AXIS_LABEL_HEIGHT = 16
    _BAR_GAP = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, int]] = []
        self.setMinimumHeight(self._CHART_HEIGHT + self._VALUE_LABEL_HEIGHT + self._AXIS_LABEL_HEIGHT)

    def set_data(self, data: list[tuple[str, int]]) -> None:
        """`data`: (category_label, value) pairs in display order, zeros
        included -- a fixed set of columns (e.g. every score 1-10) is what
        makes this a histogram with a stable shape rather than a top-N list
        like GenreBreakdownCard's."""
        self._data = data
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002 -- Qt override signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._data:
            painter.end()
            return

        count = len(self._data)
        width = self.width()
        bar_width = max(2.0, (width - self._BAR_GAP * (count + 1)) / count)
        max_value = max((value for _label, value in self._data), default=0) or 1
        baseline_y = self._VALUE_LABEL_HEIGHT + self._CHART_HEIGHT

        value_font = QFont(painter.font())
        value_font.setPointSize(8)
        value_font.setBold(True)
        axis_font = QFont(painter.font())
        axis_font.setPointSize(8)

        x = float(self._BAR_GAP)
        for label, value in self._data:
            bar_height = (value / max_value) * (self._CHART_HEIGHT - 6) if value else 0.0
            if bar_height > 0:
                bar_rect = QRectF(x, baseline_y - bar_height, bar_width, bar_height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ACCENT))
                painter.drawRoundedRect(bar_rect, 2, 2)

                painter.setFont(value_font)
                painter.setPen(QColor(TEXT_PRIMARY))
                value_rect = QRectF(
                    x - 4, baseline_y - bar_height - self._VALUE_LABEL_HEIGHT, bar_width + 8, self._VALUE_LABEL_HEIGHT
                )
                painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, str(value))

            painter.setFont(axis_font)
            painter.setPen(QColor(TEXT_SECONDARY))
            axis_rect = QRectF(x - 4, baseline_y + 4, bar_width + 8, self._AXIS_LABEL_HEIGHT)
            painter.drawText(axis_rect, Qt.AlignmentFlag.AlignCenter, label)

            x += bar_width + self._BAR_GAP
        painter.end()


class ScoreDistributionCard(QFrame):
    """A card wrapping a ColumnChart for the Dashboard's score histogram --
    always the fixed 1-10 whole-number buckets stats.py computes, so unlike
    GenreBreakdownCard there's no top-N ranking to do here, just filling in
    whichever buckets have entries."""

    _BUCKETS = range(1, 11)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("muted")
        layout.addWidget(self.title_label)

        self.chart = ColumnChart(self)
        layout.addWidget(self.chart)

        self._placeholder_label = QLabel("--", self)
        self._placeholder_label.setObjectName("muted")
        layout.addWidget(self._placeholder_label)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

        self.set_distribution({})

    def set_distribution(self, counts: dict[int, int]) -> None:
        has_data = any(counts.values())
        self.chart.setVisible(has_data)
        self._placeholder_label.setVisible(not has_data)
        self.chart.set_data([(str(bucket), counts.get(bucket, 0)) for bucket in self._BUCKETS])
        self.set_subtext("")

    def clear_values(self) -> None:
        self.set_distribution({})

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))
