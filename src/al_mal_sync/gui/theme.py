"""Design tokens + one global QSS stylesheet for the whole app.

The design skills installed under .claude/skills/ (design-system, ui-styling,
web-design-guidelines) are all React/Tailwind/CSS-variable flavored and don't
apply directly to PySide6 -- there's no CSS custom-property system, no
Tailwind utility classes, no shadcn components. What carries over is the
underlying *approach*: a small primitive color palette, one accent color,
consistent spacing/radius, and every color referenced from these named
constants rather than hardcoded per-widget -- the same "tokens, not magic
values" discipline, expressed as Python module constants feeding one QSS
string instead of CSS variables.

A single polished light theme (dark sidebar, light content), no dark-mode
toggle -- not requested, and Qt has no equivalent of prefers-color-scheme to
key off automatically.
"""

from __future__ import annotations

# -- primitives ------------------------------------------------------------

_INK_900 = "#1A1D29"
_INK_700 = "#3A3F52"
_INK_500 = "#667085"
_INK_300 = "#98A2B3"
_INK_100 = "#E4E7EC"
_INK_050 = "#F7F8FA"
_WHITE = "#FFFFFF"

_INDIGO_600 = "#4F46E5"
_INDIGO_700 = "#4338CA"
_INDIGO_100 = "#E0E7FF"

_GREEN_600 = "#12B76A"
_GREEN_100 = "#D1FADF"
_AMBER_600 = "#F79009"
_RED_600 = "#F04438"
_RED_100 = "#FEE4E2"

# -- semantic tokens ---------------------------------------------------------

SIDEBAR_BG = _INK_900
SIDEBAR_TEXT = _INK_300
SIDEBAR_TEXT_ACTIVE = _WHITE

PAGE_BG = _INK_050
SURFACE = _WHITE
BORDER = _INK_100

TEXT_PRIMARY = _INK_900
TEXT_SECONDARY = _INK_500

ACCENT = _INDIGO_600
ACCENT_HOVER = _INDIGO_700
ACCENT_SOFT = _INDIGO_100

SUCCESS = _GREEN_600
SUCCESS_SOFT = _GREEN_100
WARNING = _AMBER_600
DANGER = _RED_600
DANGER_SOFT = _RED_100

RADIUS = 8
SPACING = 12

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    color: {TEXT_PRIMARY};
}}

QMainWindow, QStackedWidget, #pageContainer {{
    background: {PAGE_BG};
}}

/* -- sidebar --------------------------------------------------------- */

QWidget#sidebarContainer {{
    background: {SIDEBAR_BG};
}}

QListWidget#sidebar {{
    background: {SIDEBAR_BG};
    border: none;
    padding: {SPACING}px 0px;
    outline: none;
}}

QListWidget#sidebar::item {{
    color: {SIDEBAR_TEXT};
    padding: 10px 20px;
    margin: 2px 8px;
    border-radius: {RADIUS}px;
}}

QListWidget#sidebar::item:hover {{
    background: rgba(255, 255, 255, 0.08);
    color: {SIDEBAR_TEXT_ACTIVE};
}}

QListWidget#sidebar::item:selected {{
    background: {ACCENT};
    color: {SIDEBAR_TEXT_ACTIVE};
}}

QLabel#sidebarTitle {{
    color: {SIDEBAR_TEXT_ACTIVE};
    font-size: 16px;
    font-weight: 600;
    padding: 8px 20px 16px 20px;
}}

/* -- cards / group boxes ---------------------------------------------- */

QGroupBox, QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    margin-top: 14px;
    padding: 14px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}

/* -- buttons ------------------------------------------------------------ */

QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 7px 16px;
    color: {TEXT_PRIMARY};
}}

QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}

QPushButton:disabled {{
    color: {TEXT_SECONDARY};
    background: {PAGE_BG};
}}

QPushButton#primaryButton {{
    background: {ACCENT};
    border: none;
    color: {_WHITE};
    font-weight: 600;
    padding: 9px 18px;
}}

QPushButton#primaryButton:hover {{
    background: {ACCENT_HOVER};
    color: {_WHITE};
}}

QPushButton#primaryButton:disabled {{
    background: {_INK_300};
    color: {_WHITE};
}}

QPushButton#dangerButton {{
    color: {DANGER};
    border-color: {DANGER_SOFT};
}}

QPushButton#dangerButton:hover {{
    border-color: {DANGER};
}}

QPushButton#linkButton {{
    border: none;
    background: transparent;
    color: {ACCENT};
    padding: 2px 0px;
    text-align: left;
}}

QPushButton#linkButton:hover {{
    color: {ACCENT_HOVER};
    text-decoration: underline;
}}

QToolButton {{
    border: none;
    background: transparent;
    color: {TEXT_SECONDARY};
    font-weight: 600;
}}

/* -- inputs -------------------------------------------------------------- */

QLineEdit, QComboBox, QSpinBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 6px 10px;
    selection-background-color: {ACCENT_SOFT};
}}

QLineEdit:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}

/* Flat chevron instead of the native OS combo-box arrow, which clashes
   with everything else here being a custom flat style. */
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}

QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_SECONDARY};
    margin-right: 10px;
}}

QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    outline: none;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
    padding: 4px;
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
    padding: 3px 0px;
}}

/* -- collapsible section header ---------------------------------------- */

QToolButton#collapsibleHeader {{
    background: {PAGE_BG};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 8px 12px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
    text-align: left;
}}

QToolButton#collapsibleHeader:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}

/* -- tables ---------------------------------------------------------- */

QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
}}

QHeaderView::section {{
    background: {PAGE_BG};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 600;
}}

/* -- progress / text views --------------------------------------------- */

QProgressBar {{
    background: {PAGE_BG};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    text-align: center;
    height: 16px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: {RADIUS}px;
}}

QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    font-family: "Cascadia Code", "Consolas", monospace;
    padding: 8px;
}}

/* -- misc ----------------------------------------------------------------- */

QLabel#statValue {{
    font-size: 26px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

QLabel#pageTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

QLabel#pageSubtitle, QLabel#muted {{
    color: {TEXT_SECONDARY};
}}

QToolTip {{
    background: {_INK_900};
    color: {_WHITE};
    border: none;
    padding: 6px 10px;
    border-radius: 6px;
}}
"""
