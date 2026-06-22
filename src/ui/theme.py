"""FreeCCR UI theme — single source of truth for color, spacing, radius, type.

Neutral dark, color-critical (no hue cast so the UI never biases perception of
image color). Installed once at app start via ``apply_theme(app)`` (see
``src/main.py``). Pure PySide6 — imports no FreeCCR widgets, so any widget may
``from ui.theme import ...`` without an import cycle.

See ``spec/visual-redesign.md`` for the design rationale and token table.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QIcon, QPixmap, QPainter

# --------------------------------------------------------------------------- #
# 3.1 Surfaces (neutral grays — zero hue)
# --------------------------------------------------------------------------- #
WINDOW = "#1e1e1e"          # app background, menu bar
CANVAS = "#1a1a1a"          # image viewport behind the photo (QGraphicsView)
PANEL = "#2a2a2a"           # side panels, dialogs, group backgrounds
SURFACE = "#333333"         # inputs, combos, line edits, unchecked buttons
SURFACE_HOVER = "#3c3c3c"   # hover state
SURFACE_ACTIVE = "#454545"  # pressed / checked / selected background
BORDER = "#3c3c3c"          # 1px control borders, separators
BORDER_STRONG = "#5a5a5a"   # emphasized borders

# 3.2 Text
TEXT = "#e0e0e0"
TEXT_MUTED = "#9a9a9a"
TEXT_DISABLED = "#6a6a6a"
TEXT_ON_ACCENT = "#ffffff"

# 3.3 Accent / focus (neutral — no colored highlight)
ACCENT = "#6e6e6e"
SELECTION_BG = "#454545"
SELECTION_TEXT = "#ffffff"

# 3.4 Semantic colors (used sparingly; theme-independent)
CH_R = "#d06666"
CH_G = "#66aa66"
CH_B = "#6688d0"
SUCCESS = "#3a8f5a"
SUCCESS_HOVER = "#46a368"
SUCCESS_PRESSED = "#2f7449"
DANGER = "#d9534f"
DANGER_HOVER = "#c9302c"
DANGER_PRESSED = "#ac2925"
BAND_COLORS = {
    "red": "#c0392b", "skin": "#d8956b", "yellow": "#c8b900",
    "green": "#27ae60", "cyan": "#17a8b4", "blue": "#2f6fd0", "purple": "#8e44ad",
}

# 3.5 Tether banner (dark amber — was light-themed)
WARN_BG = "#3a2f12"
WARN_BORDER = "#5a4a1e"
WARN_TEXT = "#e0c060"
WARN_TEXT_MUTED = "#b89a48"

# 3.6 Icons
ICON = "#cfcfcf"
ICON_DISABLED = "#6a6a6a"

# 3.7 Spacing / radius / type
SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL = 2, 4, 6, 8, 12
RADIUS_SM, RADIUS_MD, RADIUS_LG = 3, 5, 8
FS_CAPTION, FS_BODY, FS_CONTROL, FS_HEADING = 11, 12, 13, 14


class Paint:
    """3.8 UI-chrome paint constants (non-QSS; follow the dark theme)."""
    CURVE_BG = "#232323"
    CURVE_GRID = "#3d3d3d"
    CURVE_GRID_MINOR = "#333333"
    CURVE_IDENTITY = "#5a5a5a"
    CURVE_NODE = "#f0f0f0"
    CURVE_NODE_OUTLINE = "#222222"
    CURVE_NODE_DISABLED = "#777777"
    # Histogram (numpy-rendered) — kept as RGB tuples to match ccr_image.py.
    HIST_BG = (42, 42, 42)            # == PANEL #2a2a2a (keep container in sync)
    HIST_R = (230, 80, 80)
    HIST_G = (90, 200, 90)
    HIST_B = (100, 140, 235)
    HIST_PEAK = (235, 235, 235)


class Overlay:
    """3.8 On-image overlay constants (functional; values preserved verbatim).

    These sit over arbitrary photos and are tuned for contrast against image
    content, NOT the UI theme — do not re-tint to match the dark surfaces.
    """
    CURSOR_LIGHT = (255, 255, 255)
    CURSOR_DARK = (25, 25, 25)
    BWP_WHITE = (0, 100, 255, 140)
    BWP_DENSE = (255, 140, 0, 140)
    COMP_GRID = (0, 80, 0, 80)
    REF_FRAME = (255, 0, 0, 180)
    REF_FRAME_DRAG = (255, 0, 0, 120)
    SLICE = (0, 200, 255)            # alpha applied at call site (150 ghost / 235)
    DIM = (0, 0, 0, 80)
    DIM_CROP = (0, 0, 0, 110)
    BLACK_FILL = (0, 0, 0)
    DUST_STROKE = (255, 40, 40, 150)
    DUST_CURSOR = (255, 255, 255, 220)
    CROP_BORDER = (255, 255, 255, 220)
    HANDLE_FILL = (255, 255, 255, 235)
    HANDLE_OUTLINE = (30, 30, 30, 230)
    AREA_LINE = (255, 255, 255, 220)
    AREA_FEATHER = (255, 255, 255, 120)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def qcolor(v) -> QColor:
    """Coerce a hex string, (r,g,b[,a]) tuple, or QColor into a QColor."""
    if isinstance(v, QColor):
        return v
    if isinstance(v, (tuple, list)):
        return QColor(*v)
    return QColor(v)


def build_palette() -> QPalette:
    """A full dark Fusion palette derived from the tokens above."""
    pal = QPalette()
    pal.setColor(QPalette.Window, qcolor(WINDOW))
    pal.setColor(QPalette.WindowText, qcolor(TEXT))
    pal.setColor(QPalette.Base, qcolor(SURFACE))
    pal.setColor(QPalette.AlternateBase, qcolor(PANEL))
    pal.setColor(QPalette.ToolTipBase, qcolor(PANEL))
    pal.setColor(QPalette.ToolTipText, qcolor(TEXT))
    pal.setColor(QPalette.Text, qcolor(TEXT))
    pal.setColor(QPalette.Button, qcolor(SURFACE))
    pal.setColor(QPalette.ButtonText, qcolor(TEXT))
    pal.setColor(QPalette.BrightText, qcolor("#ffffff"))
    pal.setColor(QPalette.Highlight, qcolor(SELECTION_BG))
    pal.setColor(QPalette.HighlightedText, qcolor(SELECTION_TEXT))
    pal.setColor(QPalette.Link, qcolor("#9ab0c0"))
    try:
        pal.setColor(QPalette.PlaceholderText, qcolor(TEXT_MUTED))
    except AttributeError:
        pass  # older Qt without PlaceholderText role
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, qcolor(TEXT_DISABLED))
    pal.setColor(QPalette.Disabled, QPalette.Highlight, qcolor(SURFACE_ACTIVE))
    pal.setColor(QPalette.Disabled, QPalette.HighlightedText, qcolor(TEXT_DISABLED))
    return pal


# QSS uses { } literally, so format with %(name)s (QSS has no literal '%').
_QSS_TEMPLATE = """
QToolTip {
    color: %(text)s; background-color: %(panel)s;
    border: 1px solid %(border)s; padding: 3px 6px;
}

QPushButton {
    background-color: %(surface)s; color: %(text)s;
    border: 1px solid %(border)s; border-radius: %(r_sm)dpx;
    padding: 4px 10px;
}
QPushButton:hover { background-color: %(surface_hover)s; }
QPushButton:pressed, QPushButton:checked { background-color: %(surface_active)s; }
QPushButton:disabled { color: %(text_disabled)s; border-color: %(border)s; }

QToolBar { background-color: %(window)s; border: none; spacing: 2px; padding: 2px; }
QToolButton {
    background-color: transparent; color: %(text)s;
    border: none; border-radius: %(r_sm)dpx; padding: 3px;
}
QToolButton:hover { background-color: %(surface_hover)s; }
QToolButton:pressed, QToolButton:checked { background-color: %(surface_active)s; }
QToolButton:disabled { color: %(text_disabled)s; }

QComboBox {
    background-color: %(surface)s; color: %(text)s;
    border: 1px solid %(border)s; border-radius: %(r_sm)dpx; padding: 2px 6px;
}
QComboBox:hover { border-color: %(border_strong)s; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background-color: %(panel)s; color: %(text)s;
    border: 1px solid %(border)s;
    selection-background-color: %(selection_bg)s;
    selection-color: %(selection_text)s;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background-color: %(surface)s; color: %(text)s;
    border: 1px solid %(border)s; border-radius: %(r_sm)dpx; padding: 2px 4px;
    selection-background-color: %(selection_bg)s;
    selection-color: %(selection_text)s;
}
QLineEdit:focus, QSpinBox:focus, QTextEdit:focus { border-color: %(accent)s; }

QGroupBox {
    border: 1px solid %(border)s; border-radius: %(r_md)dpx;
    margin-top: 8px; padding-top: 4px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: %(text_muted)s;
}

QListWidget, QListView, QTreeView {
    background-color: %(panel)s; color: %(text)s;
    border: 1px solid %(border)s; border-radius: %(r_sm)dpx;
}
QListWidget::item:hover, QListView::item:hover { background-color: %(surface_hover)s; }
QListWidget::item:selected, QListView::item:selected {
    background-color: %(selection_bg)s; color: %(selection_text)s;
}

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background-color: %(window)s; width: 12px; margin: 0; }
QScrollBar::handle:vertical {
    background-color: %(surface_active)s; min-height: 24px;
    border-radius: 6px; margin: 2px;
}
QScrollBar::handle:vertical:hover { background-color: %(border_strong)s; }
QScrollBar:horizontal { background-color: %(window)s; height: 12px; margin: 0; }
QScrollBar::handle:horizontal {
    background-color: %(surface_active)s; min-width: 24px;
    border-radius: 6px; margin: 2px;
}
QScrollBar::handle:horizontal:hover { background-color: %(border_strong)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QProgressBar {
    background-color: %(surface)s; color: %(text)s;
    border: 1px solid %(border)s; border-radius: %(r_sm)dpx;
    text-align: center;
}
QProgressBar::chunk { background-color: %(accent)s; border-radius: %(r_sm)dpx; }

QSlider::groove:horizontal {
    height: 4px; background-color: %(surface)s; border-radius: 2px;
}
QSlider::sub-page:horizontal { background-color: %(border_strong)s; border-radius: 2px; }
QSlider::handle:horizontal {
    background-color: %(text_muted)s; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background-color: %(text)s; }

QMenuBar { background-color: %(window)s; color: %(text)s; }
QMenuBar::item { background: transparent; padding: 4px 8px; }
QMenuBar::item:selected { background-color: %(surface_hover)s; }
QMenu { background-color: %(panel)s; color: %(text)s; border: 1px solid %(border)s; }
QMenu::item { padding: 4px 22px; }
QMenu::item:selected { background-color: %(selection_bg)s; color: %(selection_text)s; }
QMenu::item:disabled { color: %(text_disabled)s; }
QMenu::separator { height: 1px; background-color: %(border)s; margin: 4px 10px; }

QCheckBox, QRadioButton { color: %(text)s; spacing: 6px; }
QCheckBox:disabled, QRadioButton:disabled { color: %(text_disabled)s; }

QTabWidget::pane { border: 1px solid %(border)s; }
QTabBar::tab {
    background-color: %(surface)s; color: %(text_muted)s;
    padding: 4px 10px; border: 1px solid %(border)s; border-bottom: none;
}
QTabBar::tab:selected { background-color: %(panel)s; color: %(text)s; }

QGraphicsView { background-color: %(canvas)s; border: none; }

QLabel[caption="true"] { color: %(text_muted)s; font-size: %(fs_caption)dpx; }
"""


def global_qss() -> str:
    """The central stylesheet, built from the tokens above (one source of truth)."""
    return _QSS_TEMPLATE % {
        "window": WINDOW, "canvas": CANVAS, "panel": PANEL, "surface": SURFACE,
        "surface_hover": SURFACE_HOVER, "surface_active": SURFACE_ACTIVE,
        "border": BORDER, "border_strong": BORDER_STRONG,
        "text": TEXT, "text_muted": TEXT_MUTED, "text_disabled": TEXT_DISABLED,
        "accent": ACCENT, "selection_bg": SELECTION_BG, "selection_text": SELECTION_TEXT,
        "r_sm": RADIUS_SM, "r_md": RADIUS_MD, "fs_caption": FS_CAPTION,
    }


def load_tinted_icon(abs_path: str, color: str = ICON,
                     disabled_color: str = ICON_DISABLED) -> QIcon:
    """Load a monochrome PNG and recolor it using its alpha as a mask.

    The toolbar icons are pure-black line art on transparency (invisible on a
    dark theme); this paints them ``color`` (and a dimmer ``disabled_color`` for
    the disabled state) so they read on dark surfaces.
    """
    base = QPixmap(abs_path)
    if base.isNull():
        return QIcon(abs_path)

    def _tint(pm: QPixmap, col: str) -> QPixmap:
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.drawPixmap(0, 0, pm)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(out.rect(), qcolor(col))
        p.end()
        return out

    icon = QIcon()
    icon.addPixmap(_tint(base, color), QIcon.Normal)
    icon.addPixmap(_tint(base, disabled_color), QIcon.Disabled)
    return icon


def apply_theme(app, settings=None) -> None:
    """Install the global dark theme on the QApplication.

    Fusion is required: the native Windows/macOS style ignores QPalette for many
    controls; Fusion honors it fully. Call once, before building the main window.
    ``settings`` is reserved for a future theme preference; v1 always applies dark.
    """
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(global_qss())
