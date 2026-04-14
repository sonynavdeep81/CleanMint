"""
ui/theme.py — CleanMint Theme Engine

Mint-green dark theme (default) and light theme.
Apply via: app.setStyleSheet(Theme.stylesheet())
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    # Backgrounds
    bg_primary: str
    bg_secondary: str
    bg_card: str
    bg_hover: str
    bg_sidebar: str

    # Text
    text_primary: str
    text_secondary: str
    text_muted: str

    # Accent (mint green)
    accent: str
    accent_hover: str
    accent_text: str       # text ON accent-colored buttons

    # Status colours
    success: str
    warning: str
    danger: str
    info: str

    # Borders
    border: str
    border_focus: str

    # Risk badge colours
    risk_low_bg: str
    risk_low_fg: str
    risk_medium_bg: str
    risk_medium_fg: str
    risk_expert_bg: str
    risk_expert_fg: str


DARK = Palette(
    bg_primary="#1a1f2e",
    bg_secondary="#242938",
    bg_card="#2d3348",
    bg_hover="#353c55",
    bg_sidebar="#151924",

    text_primary="#e8eaf0",
    text_secondary="#c4cce0",
    text_muted="#9ba5bf",

    accent="#3ddc84",
    accent_hover="#34c474",
    accent_text="#0d1117",

    success="#3ddc84",
    warning="#f0a500",
    danger="#e05252",
    info="#5b9cf6",

    border="#3a4060",
    border_focus="#3ddc84",

    risk_low_bg="#1a3a2a",
    risk_low_fg="#3ddc84",
    risk_medium_bg="#3a2d10",
    risk_medium_fg="#f0a500",
    risk_expert_bg="#3a1a1a",
    risk_expert_fg="#e05252",
)

LIGHT = Palette(
    bg_primary="#f4f6fb",
    bg_secondary="#ffffff",
    bg_card="#ffffff",
    bg_hover="#eef1f8",
    bg_sidebar="#e8ecf5",

    text_primary="#1a1f2e",
    text_secondary="#3d4460",
    text_muted="#8a92aa",

    accent="#1db563",
    accent_hover="#18a357",
    accent_text="#ffffff",

    success="#1db563",
    warning="#d4820a",
    danger="#c73c3c",
    info="#2a72e5",

    border="#d0d5e8",
    border_focus="#1db563",

    risk_low_bg="#e6f9ef",
    risk_low_fg="#1db563",
    risk_medium_bg="#fff4e0",
    risk_medium_fg="#d4820a",
    risk_expert_bg="#fde8e8",
    risk_expert_fg="#c73c3c",
)


class Theme:
    _current: Palette = DARK

    @classmethod
    def set_dark(cls):
        cls._current = DARK

    @classmethod
    def set_light(cls):
        cls._current = LIGHT

    @classmethod
    def is_dark(cls) -> bool:
        return cls._current is DARK

    @classmethod
    def p(cls) -> Palette:
        return cls._current

    @classmethod
    def stylesheet(cls) -> str:
        p = cls._current
        return f"""
/* ── Global ─────────────────────────────────────────── */
QWidget {{
    background-color: {p.bg_primary};
    color: {p.text_primary};
    font-family: "Inter", "Segoe UI", "Ubuntu", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {p.bg_primary};
}}

/* ── Sidebar ─────────────────────────────────────────── */
#Sidebar {{
    background-color: {p.bg_sidebar};
    border-right: 1px solid {p.border};
}}

#SidebarBtn {{
    background: transparent;
    color: {p.text_secondary};
    border: none;
    text-align: left;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 13px;
}}
#SidebarBtn:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
#SidebarBtn[active="true"] {{
    background-color: {p.bg_card};
    color: {p.accent};
    font-weight: 600;
    border-left: 3px solid {p.accent};
}}

/* ── Cards ───────────────────────────────────────────── */
#Card {{
    background-color: {p.bg_card};
    border: 1px solid {p.border};
    border-radius: 12px;
    padding: 16px;
}}

/* ── Primary button ──────────────────────────────────── */
#PrimaryBtn {{
    background-color: {p.accent};
    color: {p.accent_text};
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 600;
}}
#PrimaryBtn:hover {{
    background-color: {p.accent_hover};
}}
#PrimaryBtn:pressed {{
    background-color: {p.accent_hover};
    padding-top: 11px;
}}
#PrimaryBtn:disabled {{
    background-color: {p.border};
    color: {p.text_muted};
}}

/* ── Secondary button ───────────────────────────────── */
#SecondaryBtn {{
    background-color: transparent;
    color: {p.text_secondary};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 13px;
}}
#SecondaryBtn:hover {{
    border-color: {p.accent};
    color: {p.accent};
}}

/* ── Danger button ───────────────────────────────────── */
#DangerBtn {{
    background-color: transparent;
    color: {p.danger};
    border: 1px solid {p.danger};
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
}}
#DangerBtn:hover {{
    background-color: {p.danger};
    color: #ffffff;
}}
#DangerBtn:disabled {{
    color: {p.text_muted};
    border-color: {p.border};
}}

/* ── Progress bar ────────────────────────────────────── */
QProgressBar {{
    background-color: {p.bg_secondary};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: 4px;
}}

/* ── Checkboxes ──────────────────────────────────────── */
QCheckBox {{
    color: {p.text_primary};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {p.border};
    background: {p.bg_secondary};
}}
QCheckBox::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
}}

/* ── ScrollBar ───────────────────────────────────────── */
QScrollBar:vertical {{
    background: {p.bg_primary};
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p.text_muted};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

/* ── Labels ──────────────────────────────────────────── */
#TitleLabel {{
    font-size: 22px;
    font-weight: 700;
    color: {p.text_primary};
}}
#SubtitleLabel {{
    font-size: 13px;
    color: {p.text_secondary};
}}
#AccentLabel {{
    color: {p.accent};
    font-weight: 600;
}}
#MutedLabel {{
    color: {p.text_muted};
    font-size: 12px;
}}
#SectionHeader {{
    font-size: 15px;
    font-weight: 600;
    color: {p.text_primary};
}}

/* ── Separator ───────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {p.border};
}}

/* ── Table / List ────────────────────────────────────── */
QTableWidget, QListWidget, QTreeWidget {{
    background-color: {p.bg_card};
    border: 1px solid {p.border};
    border-radius: 8px;
    gridline-color: {p.border};
    outline: none;
}}
QTableWidget::item, QListWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {p.border};
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QHeaderView::section {{
    background-color: {p.bg_secondary};
    color: {p.text_secondary};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 10px;
    font-weight: 600;
    font-size: 12px;
}}

/* ── Toggle Switch (QCheckBox styled) ───────────────── */
#ToggleSwitch {{
    font-size: 13px;
    color: {p.text_primary};
    spacing: 10px;
}}

/* ── Input fields ────────────────────────────────────── */
QLineEdit, QSpinBox, QComboBox {{
    background-color: {p.bg_secondary};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {p.border_focus};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border};
    selection-background-color: {p.bg_hover};
}}

/* ── Tooltip ─────────────────────────────────────────── */
QToolTip {{
    background-color: {p.bg_card};
    color: {p.text_primary};
    border: 1px solid {p.border};
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
}}
"""
