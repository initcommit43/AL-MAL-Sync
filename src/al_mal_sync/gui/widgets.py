"""Small reusable widgets/helpers shared across pages: a collapsible section
(used by Sync's "Advanced options" and Mapping Issues' "Manual overrides"),
a stat card (used by the Dashboard's tiles), and two layout helpers.

The layout helpers exist because Qt's QVBoxLayout/QFormLayout stretch every
child to the full width of their parent by default -- with no page-level
width cap, a QComboBox or QPushButton ends up spanning the *entire* window
width on anything wider than a laptop screen, which reads as broken/
unstyled rather than deliberate. cap_width()/left_aligned() are the fix,
applied at each call site rather than through a page-wide max-width wrapper,
since some pages (tables, log panels) genuinely want the full width and
others (forms, buttons) don't.
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


class StatCard(QFrame):
    """A small card: a muted title, a big value, and an optional colored
    sub-label (e.g. a diff or status note)."""

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

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)

    def set_subtext(self, text: str, *, color: str | None = None) -> None:
        self.subtext_label.setText(text)
        self.subtext_label.setStyleSheet(f"color: {color};" if color else "")
