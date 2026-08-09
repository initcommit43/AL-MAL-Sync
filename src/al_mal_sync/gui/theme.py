"""Design tokens + one global QSS stylesheet for the whole app.

Palette and component language lifted from design.md (an AniList-dashboard-
derived spec): a near-black navy background, a single bright blue accent
reserved for interactive elements/links, low-saturation surfaces, and
separation via background-shade contrast rather than visible borders --
cards, sidebar rows, and table headers all read as "a slightly lighter slab
of navy" instead of a boxed-in panel. Genre-tag colors from the spec are
repurposed here as semantic status colors and as the AniList/MyAnimeList
brand-color pair used to visually tell the two platforms apart wherever both
show up side by side (dashboard stat cards, source-breakdown bars).

The design skills installed under .claude/skills/ (design-system, ui-styling,
web-design-guidelines) are all React/Tailwind/CSS-variable flavored and don't
apply directly to PySide6 -- there's no CSS custom-property system, no
Tailwind utility classes, no shadcn components. What carries over is the
underlying *approach*: a small primitive color palette, one accent color,
consistent spacing/radius, and every color referenced from these named
constants rather than hardcoded per-widget -- the same "tokens, not magic
values" discipline, expressed as Python module constants feeding one QSS
string instead of CSS variables.

A single dark theme, no light-mode toggle -- not requested, and Qt has no
equivalent of prefers-color-scheme to key off automatically.
"""

from __future__ import annotations

# -- primitives (design.md palette) -----------------------------------------

_BG_PRIMARY = "#0b1622"
_BG_SURFACE = "#12202f"
_BG_SURFACE_ALT = "#1a2b3d"
_BG_SURFACE_HOVER = "#213a4f"

_TEXT_PRIMARY = "#e6edf3"
_TEXT_SECONDARY = "#8b9bab"

_ACCENT_BLUE = "#3db4f2"
_ACCENT_BLUE_HOVER = "#2f9bd6"
_ACCENT_BLUE_SOFT = "#1d3a4f"
_ACCENT_CYAN = "#4fc3d9"

_TAG_ACTION_GREEN = "#4caf50"
_TAG_DRAMA_RED = "#f28ba3"
_TAG_FANTASY_AMBER = "#f2a13d"
_BADGE_DISCUSSION_PURPLE = "#7e5bef"

_WHITE = "#ffffff"

# -- semantic tokens ---------------------------------------------------------

PAGE_BG = _BG_PRIMARY
SURFACE = _BG_SURFACE
SURFACE_ALT = _BG_SURFACE_ALT
SURFACE_HOVER = _BG_SURFACE_HOVER
DIVIDER = _BG_SURFACE_ALT

SIDEBAR_BG = _BG_SURFACE_ALT
SIDEBAR_TEXT = _TEXT_SECONDARY
SIDEBAR_TEXT_ACTIVE = _WHITE

TEXT_PRIMARY = _TEXT_PRIMARY
TEXT_SECONDARY = _TEXT_SECONDARY

ACCENT = _ACCENT_BLUE
ACCENT_HOVER = _ACCENT_BLUE_HOVER
ACCENT_SOFT = _ACCENT_BLUE_SOFT
ACCENT_CYAN = _ACCENT_CYAN

SUCCESS = _TAG_ACTION_GREEN
WARNING = _TAG_FANTASY_AMBER
DANGER = _TAG_DRAMA_RED

# AniList vs MyAnimeList brand-color pair -- used wherever both platforms'
# numbers appear side by side (dashboard stat cards, source-breakdown bars),
# per design.md 7's "swap genre tag colors for AL/MAL brand colors" note.
ANILIST_COLOR = _ACCENT_BLUE
MYANIMELIST_COLOR = _BADGE_DISCUSSION_PURPLE

RADIUS = 10
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

/* A page wrapped in a scroll area (see dashboard_tab.py's "isn't scrollable
   and cuts content off" fix) -- QScrollArea's own frame and its viewport
   both default to a plain widget background that doesn't inherit PAGE_BG,
   which shows as a mismatched rectangle behind the page content otherwise. */
QScrollArea#pageScrollArea, QScrollArea#pageScrollArea > QWidget > QWidget {{
    background: {PAGE_BG};
    border: none;
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
    background: {SURFACE_HOVER};
    color: {SIDEBAR_TEXT_ACTIVE};
}}

QListWidget#sidebar::item:selected {{
    background: {ACCENT};
    color: {_BG_PRIMARY};
    font-weight: 600;
}}

QLabel#sidebarTitle {{
    color: {SIDEBAR_TEXT_ACTIVE};
    font-size: 16px;
    font-weight: 600;
    padding: 8px 20px 16px 20px;
}}

/* -- cards / group boxes ------------------------------------------------
   No visible border -- separation from the page background comes from the
   surface shade alone, per design.md's "no card border ... separation via
   background shade against page background" note. */

QGroupBox, QFrame#card {{
    background: {SURFACE};
    border: none;
    border-radius: {RADIUS}px;
    margin-top: 28px;
    padding: 14px;
}}

/* A StatCard draws its own title inside the frame (see widgets.py), so it
   never needs the 28px band QGroupBox's *floating* title reserves above it.
   That's invisible when a single StatCard sits under a page-level heading
   (the library-size cards), but once several are packed into one grid (the
   Dashboard's Library Stats section) it stacks up into a very visible dead
   band between rows -- this variant is opted into per-card via the
   "compact" property for exactly that case. */
QFrame#card[compact="true"] {{
    margin-top: 0px;
}}

/* Without a visible frame line for the title to straddle (the classic
   bordered-groupbox look design.md explicitly avoids), the title needs its
   own reserved band clearly taller than one line of 15px bold text --
   otherwise it overflows down into the surface rectangle below it instead
   of floating above it as a section heading. margin-top above is that
   band; this subcontrol-position/top pairing anchors the title to the very
   top of it instead of vertically centering (the default), which otherwise
   still crowds the box when the band is this much taller than the text. */
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    top: 0px;
    left: 2px;
    padding: 0 4px;
    color: {TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 600;
}}

/* -- dividers ------------------------------------------------------------ */

QFrame#divider {{
    background: {DIVIDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* -- pills / badges ------------------------------------------------------- */

QLabel[pill="true"] {{
    border-radius: 9px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 600;
    color: {_WHITE};
}}

QLabel[pillKind="success"] {{ background: {SUCCESS}; }}
QLabel[pillKind="warning"] {{ background: {WARNING}; color: {_BG_PRIMARY}; }}
QLabel[pillKind="danger"] {{ background: {DANGER}; color: {_BG_PRIMARY}; }}
QLabel[pillKind="accent"] {{ background: {ACCENT}; color: {_BG_PRIMARY}; }}
QLabel[pillKind="neutral"] {{ background: {SURFACE_ALT}; color: {TEXT_SECONDARY}; }}
QLabel[pillKind="anilist"] {{ background: {ANILIST_COLOR}; color: {_BG_PRIMARY}; }}
QLabel[pillKind="myanimelist"] {{ background: {MYANIMELIST_COLOR}; }}

/* Same pillKind color tokens, applied to a QFrame instead of a QLabel --
   StatusDistributionBar's chart segments and StatusBreakdownCard's legend
   dots (see widgets.py) are plain colored rectangles/squares, not pill
   chips, so they skip the padding/font rules above. */
QFrame[pillKind="success"] {{ background: {SUCCESS}; }}
QFrame[pillKind="warning"] {{ background: {WARNING}; }}
QFrame[pillKind="danger"] {{ background: {DANGER}; }}
QFrame[pillKind="accent"] {{ background: {ACCENT}; }}
QFrame[pillKind="neutral"] {{ background: {SURFACE_ALT}; }}

/* The generic "neutral" token (SURFACE_ALT) is deliberately close to the
   page background for pills -- a near-invisible chip reads as "no strong
   state". That's exactly wrong for a chart segment: it reads as a gap in
   the bar, not as "planning" having a real share of the list. Bump it to
   the lighter secondary-text tone specifically here; the ID+attribute
   selector is more specific than the plain QFrame[pillKind="neutral"] rule
   above, so it wins for these two widgets without touching neutral pills
   anywhere else. */
QFrame#statusBarSegment[pillKind="neutral"], QFrame#legendDot[pillKind="neutral"] {{
    background: {TEXT_SECONDARY};
}}

QFrame#statusBarSegment {{
    border-radius: 3px;
}}

QFrame#legendDot {{
    border-radius: 4px;
}}

QLabel#legendValue {{
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

/* -- buttons ------------------------------------------------------------ */

QPushButton {{
    background: {SURFACE_ALT};
    border: none;
    border-radius: {RADIUS}px;
    padding: 7px 16px;
    color: {TEXT_PRIMARY};
}}

QPushButton:hover {{
    background: {SURFACE_HOVER};
    color: {ACCENT};
}}

QPushButton:disabled {{
    color: {TEXT_SECONDARY};
    background: {SURFACE};
}}

QPushButton#primaryButton {{
    background: {ACCENT};
    border: none;
    color: {_BG_PRIMARY};
    font-weight: 600;
    padding: 9px 18px;
}}

QPushButton#primaryButton:hover {{
    background: {ACCENT_HOVER};
    color: {_BG_PRIMARY};
}}

QPushButton#primaryButton:disabled {{
    background: {SURFACE_ALT};
    color: {TEXT_SECONDARY};
}}

QPushButton#dangerButton {{
    color: {DANGER};
}}

QPushButton#dangerButton:hover {{
    background: {SURFACE_HOVER};
    color: {DANGER};
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
    background: {_BG_PRIMARY};
    border: 1px solid {SURFACE_ALT};
    border-radius: {RADIUS}px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_SOFT};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {TEXT_SECONDARY};
    border-color: {SURFACE};
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
    background: {SURFACE_ALT};
    border: none;
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
    background: {SURFACE_ALT};
    border: none;
    border-radius: {RADIUS}px;
    padding: 8px 12px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
    text-align: left;
}}

QToolButton#collapsibleHeader:hover {{
    background: {SURFACE_HOVER};
    color: {ACCENT};
}}

QToolButton#collapsibleHeader:disabled {{
    color: {TEXT_SECONDARY};
}}

/* -- tables ---------------------------------------------------------- */

QTableWidget {{
    background: {SURFACE};
    border: none;
    border-radius: {RADIUS}px;
    gridline-color: {DIVIDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
    alternate-background-color: {SURFACE_ALT};
}}

/* The section-only rule below doesn't cover the header viewport's own
   background past the last section (visible as a stray light-gray strip
   under a short vertical row-number header) -- this covers that. */
QHeaderView {{
    background: {SURFACE};
}}

QHeaderView::section {{
    background: {SURFACE};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {DIVIDER};
    padding: 6px 8px;
    font-weight: 600;
}}

QTableCornerButton::section {{
    background: {SURFACE};
    border: none;
}}

/* -- scrollbars ----------------------------------------------------------
   The native scrollbar is light gray and clashes hard with a dark theme --
   flat, thin, and colored from the same token set as everything else. */

QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {SURFACE_ALT};
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {SURFACE_HOVER};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: {SURFACE_ALT};
    border-radius: 5px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {SURFACE_HOVER};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* -- progress / text views --------------------------------------------- */

QProgressBar {{
    background: {SURFACE};
    border: none;
    border-radius: {RADIUS}px;
    text-align: center;
    height: 16px;
    color: {TEXT_PRIMARY};
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: {RADIUS}px;
}}

QPlainTextEdit {{
    background: {SURFACE};
    border: none;
    border-radius: {RADIUS}px;
    color: {TEXT_PRIMARY};
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

/* A subsection heading within a page/group (e.g. the Dashboard's "Anime" /
   "Manga" stats columns) -- bold and a step up from body text, but not
   colored: this app reserves color for a single interactive accent plus
   semantic status tones (see the module docstring), so two side-by-side
   headings both fighting for "the" accent blue would just look like a
   mistake rather than a deliberate distinction. Physical position (left
   column vs right column) is what actually separates them. */
QLabel#sectionHeading {{
    font-size: 15px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}

QLabel#pageSubtitle, QLabel#muted {{
    color: {TEXT_SECONDARY};
}}

QToolTip {{
    background: {SURFACE_ALT};
    color: {TEXT_PRIMARY};
    border: none;
    padding: 6px 10px;
    border-radius: 6px;
}}
"""
