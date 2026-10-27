"""Reusable building blocks for Tidy's pages."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from tidy.ui import theme


class Page(QWidget):
    """A page with a title, subtitle and a content layout."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        titles.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        header.addLayout(self.header_actions)
        outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, padding: int = 16) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(padding, padding, padding, padding)
        self.layout_.setSpacing(8)


class StatCard(Card):
    def __init__(self, label: str, value: str = "—", hint: str = "") -> None:
        super().__init__()
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        self.label = QLabel(label)
        self.label.setObjectName("StatLabel")
        self.hint = QLabel(hint)
        self.hint.setObjectName("Muted")
        self.hint.setVisible(bool(hint))
        self.layout_.addWidget(self.label)
        self.layout_.addWidget(self.value)
        self.layout_.addWidget(self.hint)
        self.setMinimumWidth(150)

    def set(self, value: str, hint: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


class Banner(QFrame):
    """An inline message strip (info / warning)."""

    def __init__(self, text: str = "", kind: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Banner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label, 1)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.label.setObjectName({"warning": "Warning", "danger": "Danger"}.get(kind, ""))
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


class EmptyState(QWidget):
    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m = QLabel(message)
        m.setObjectName("Muted")
        m.setWordWrap(True)
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(t)
        layout.addWidget(m)
        layout.addStretch(1)


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class ConfidenceDelegate(QStyledItemDelegate):
    """Paints a 0..1 value as a slim bar with a percentage."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        value = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(value, float):
            super().paint(painter, option, index)
            return
        p = theme.current()
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(p.selection))
        rect = option.rect.adjusted(8, 0, -8, 0)
        bar_w = max(30, rect.width() - 44)
        bar = QRect(rect.left(), rect.center().y() - 3, bar_w, 6)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.border))
        painter.drawRoundedRect(bar, 3, 3)
        colour = p.success if value >= 0.8 else p.accent if value >= 0.55 else p.warning
        painter.setBrush(QColor(colour))
        filled = QRect(bar.left(), bar.top(), max(4, int(bar.width() * value)), bar.height())
        painter.drawRoundedRect(filled, 3, 3)
        painter.setPen(QColor(p.muted))
        text_rect = QRect(bar.right() + 6, rect.top(), 40, rect.height())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"{round(value * 100)}%",
        )
        painter.restore()
