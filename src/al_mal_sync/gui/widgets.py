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
        min_value_width = 0
        for row_label in row_labels:
            row = QHBoxLayout()
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

            layout.addLayout(row)
            self._value_labels[row_label] = value

        self.subtext_label = QLabel("", self)
        self.subtext_label.setObjectName("muted")
        self.subtext_label.setWordWrap(True)
        self.subtext_label.setVisible(False)
        layout.addWidget(self.subtext_label)

    def set_value(self, row_label: str, value: int | str) -> None:
        self._value_labels[row_label].setText(str(value))

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
        self.subtext_label.setVisible(bool(text))
