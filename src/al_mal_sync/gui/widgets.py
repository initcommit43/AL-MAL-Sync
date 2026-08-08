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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QToolButton, QVBoxLayout, QWidget

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

    def __init__(self, title: str, row_labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")

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


class StatusDistributionBar(QWidget):
    """A thin segmented horizontal bar: one colored segment per non-zero
    status count, sized proportionally to that count's share of the total --
    a lightweight stand-in for a full chart library (no QtCharts dependency
    here). QHBoxLayout stretch factors do the proportional math, so this is
    plain layout, not custom painting. Segment colors reuse the same
    `pillKind` token vocabulary as Pill (theme.py's QFrame[pillKind=...]
    rules mirror the QLabel ones), just applied to a QFrame instead."""

    _HEIGHT = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.setFixedHeight(self._HEIGHT)

    def set_counts(self, segments: list[tuple[str, int]]) -> None:
        """`segments`: (pillKind, count) pairs in display order. Zero-count
        segments are skipped rather than rendered as an invisible sliver."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        total = sum(count for _kind, count in segments)
        if total <= 0:
            empty = QFrame(self)
            empty.setObjectName("statusBarSegment")
            empty.setProperty("pillKind", "neutral")
            self._layout.addWidget(empty)
            return

        for kind, count in segments:
            if count <= 0:
                continue
            segment = QFrame(self)
            segment.setObjectName("statusBarSegment")
            segment.setProperty("pillKind", kind)
            self._layout.addWidget(segment, count)


class StatusBreakdownCard(QFrame):
    """A card pairing a StatusDistributionBar with a legend of exact counts
    -- the Dashboard's Anime/Manga Status widgets. `segments` fixes the
    bucket order/labels/colors once at construction (the same status bucket
    reads as "Watching" for anime and "Reading" for manga, e.g.); set_counts
    just supplies the numbers per refresh."""

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

        self.bar = StatusDistributionBar(self)
        layout.addWidget(self.bar)

        legend = QVBoxLayout()
        legend.setSpacing(6)
        self._legend_value_labels: dict[str, QLabel] = {}
        for bucket_key, display_label, kind in segments:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QFrame(self)
            dot.setObjectName("legendDot")
            dot.setProperty("pillKind", kind)
            dot.setFixedSize(8, 8)
            row.addWidget(dot)
            label = QLabel(display_label, self)
            label.setObjectName("muted")
            row.addWidget(label)
            row.addStretch(1)
            value = QLabel("--", self)
            value.setObjectName("legendValue")
            row.addWidget(value)
            legend.addLayout(row)
            self._legend_value_labels[bucket_key] = value
        layout.addLayout(legend)

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

    def set_counts(self, counts: dict[str, int]) -> None:
        self.bar.set_counts([(kind, counts.get(key, 0)) for key, _label, kind in self._segments])
        for bucket_key, _label, _kind in self._segments:
            self._legend_value_labels[bucket_key].setText(str(counts.get(bucket_key, 0)))
        self.set_subtext("")

    def clear_values(self) -> None:
        self.bar.set_counts([])
        for value in self._legend_value_labels.values():
            value.setText("--")

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))
