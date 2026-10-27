"""Colour tokens and the application stylesheet (dark by default)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    success: str
    warning: str
    danger: str
    selection: str


DARK = Palette(
    name="dark",
    bg="#0f1115",
    surface="#161a21",
    surface_alt="#1d222b",
    border="#272d38",
    text="#e7e9ee",
    muted="#8e97a4",
    accent="#7c93ff",
    accent_text="#0b0d12",
    success="#41c08b",
    warning="#e3ad52",
    danger="#ec6a72",
    selection="#26304a",
)
LIGHT = Palette(
    name="light",
    bg="#f4f5f8",
    surface="#ffffff",
    surface_alt="#f0f2f6",
    border="#dde1e8",
    text="#1b1f27",
    muted="#646e7c",
    accent="#4a63e7",
    accent_text="#ffffff",
    success="#1f9d68",
    warning="#b97a10",
    danger="#cf3f49",
    selection="#dfe5ff",
)
PALETTES = {"dark": DARK, "light": LIGHT}
_current: Palette = DARK


def current() -> Palette:
    return _current


def font_family() -> str:
    return "Segoe UI Variable Text, Segoe UI, Inter, -apple-system, Helvetica Neue, Arial"


ASSETS = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    return (ASSETS / name).as_posix()


def stylesheet(p: Palette) -> str:
    check = _asset(f"check-{p.accent_text.lstrip('#')}.svg")
    return f"""
    * {{ font-family: {font_family()}; font-size: 10pt; color: {p.text}; }}
    QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
        background: {p.bg};
    }}
    QWidget#Sidebar {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
    QLabel#Brand {{ font-size: 15pt; font-weight: 600; padding: 4px 8px; }}
    QLabel#BrandSub {{ color: {p.muted}; padding: 0 8px; font-size: 9pt; }}
    QPushButton#NavButton {{
        text-align: left; padding: 9px 14px; border: none; border-radius: 8px;
        background: transparent; color: {p.muted}; font-weight: 500;
    }}
    QPushButton#NavButton:hover {{ background: {p.surface_alt}; color: {p.text}; }}
    QPushButton#NavButton:checked {{ background: {p.selection}; color: {p.text}; }}

    QLabel#PageTitle {{ font-size: 18pt; font-weight: 600; }}
    QLabel#PageSubtitle, QLabel#Muted {{ color: {p.muted}; }}
    QLabel#SectionTitle {{ font-size: 11pt; font-weight: 600; }}
    QLabel#StatValue {{ font-size: 20pt; font-weight: 600; }}
    QLabel#StatLabel {{ color: {p.muted}; font-size: 9pt; }}
    QLabel#Badge {{
        background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 9px;
        padding: 2px 8px; color: {p.muted}; font-size: 9pt;
    }}
    QLabel#Warning {{ color: {p.warning}; }}
    QLabel#Danger {{ color: {p.danger}; }}
    QLabel#Success {{ color: {p.success}; }}

    QFrame#Card {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
    }}
    QFrame#Banner {{
        background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 10px;
    }}

    QPushButton {{
        background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 8px;
        padding: 7px 14px; font-weight: 500;
    }}
    QPushButton:hover {{ border-color: {p.accent}; }}
    QPushButton:pressed {{ background: {p.selection}; }}
    QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; background: {p.surface}; }}
    QPushButton#Primary {{
        background: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent};
    }}
    QPushButton#Primary:hover {{ background: {p.accent}; border-color: {p.text}; }}
    QPushButton#Primary:disabled {{
        background: {p.surface_alt}; color: {p.muted}; border-color: {p.border};
    }}
    QPushButton#Danger {{ color: {p.danger}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 8px;
        padding: 6px 8px; selection-background-color: {p.selection};
    }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {p.accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface}; border: 1px solid {p.border};
        selection-background-color: {p.selection}; outline: none;
    }}

    QTableView, QTreeView, QListWidget {{
        background: {p.surface}; alternate-background-color: {p.surface_alt};
        border: 1px solid {p.border}; border-radius: 10px; gridline-color: transparent;
        selection-background-color: {p.selection}; selection-color: {p.text}; outline: none;
    }}
    QTableView::item, QTreeView::item {{ padding: 4px 6px; border: none; }}
    QHeaderView::section {{
        background: {p.surface}; color: {p.muted}; border: none;
        border-bottom: 1px solid {p.border}; padding: 7px 6px; font-weight: 600; font-size: 9pt;
    }}
    QTableCornerButton::section {{ background: {p.surface}; border: none; }}

    QProgressBar {{
        background: {p.surface_alt}; border: none; border-radius: 4px; height: 8px;
        text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator, QTableView::indicator {{
        width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.muted};
        background: {p.surface_alt};
    }}
    QCheckBox::indicator:checked, QTableView::indicator:checked {{
        background: {p.accent}; border-color: {p.accent}; image: url("{check}");
    }}
    QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {p.text}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QGroupBox {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px;
        margin-top: 14px; padding: 16px 14px 12px 14px; font-weight: 600;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 4px; }}
    QToolTip {{
        background: {p.surface_alt}; color: {p.text}; border: 1px solid {p.border};
        padding: 6px; border-radius: 6px;
    }}
    QStatusBar {{ background: {p.surface}; border-top: 1px solid {p.border}; color: {p.muted}; }}
    QSplitter::handle {{ background: {p.bg}; }}
    QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.selection}; }}
    QDialog {{ background: {p.bg}; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{
        background: transparent; color: {p.muted}; padding: 8px 14px; margin-right: 4px;
        border: none; border-bottom: 2px solid transparent; font-weight: 500;
    }}
    QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
    QTabBar::tab:hover {{ color: {p.text}; }}
    """


def apply(app: QApplication, name: str) -> Palette:
    global _current
    palette = PALETTES.get(name, DARK)
    _current = palette
    app.setStyle("Fusion")
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(palette.bg))
    qp.setColor(QPalette.ColorRole.Base, QColor(palette.surface))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(palette.surface_alt))
    qp.setColor(QPalette.ColorRole.Text, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.Button, QColor(palette.surface_alt))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(palette.selection))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor(palette.text))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(palette.muted))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette.surface_alt))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(palette.text))
    app.setPalette(qp)
    font = QFont()
    font.setFamilies([f.strip() for f in font_family().split(",")])
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(stylesheet(palette))
    return palette
