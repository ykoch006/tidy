"""History: past operations, their individual moves, and undo."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tidy.core.history import MoveState, OperationRecord, OpStatus
from tidy.core.organiser import OperationResult
from tidy.ui import theme
from tidy.ui.context import AppContext
from tidy.ui.widgets import Banner, Page, muted, section_title
from tidy.utils.formatting import human_size

STATUS_LABELS = {
    OpStatus.PENDING: "Pending",
    OpStatus.RUNNING: "Running",
    OpStatus.COMPLETED: "Completed",
    OpStatus.PARTIAL: "Completed with issues",
    OpStatus.CANCELLED: "Stopped",
    OpStatus.UNDOING: "Undoing",
    OpStatus.UNDONE: "Undone",
    OpStatus.PARTIALLY_UNDONE: "Partly undone",
    OpStatus.RECOVERED: "Recovered after interruption",
}
MOVE_LABELS = {
    MoveState.PENDING: "Pending",
    MoveState.IN_PROGRESS: "In progress",
    MoveState.DONE: "Moved",
    MoveState.FAILED: "Failed",
    MoveState.SKIPPED: "Skipped",
    MoveState.UNDO_IN_PROGRESS: "Restoring",
    MoveState.UNDONE: "Restored",
    MoveState.UNDO_FAILED: "Restore failed",
}


def _local(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class HistoryPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            "History",
            "Every organisation Tidy has performed. Undo restores files to where they were.",
        )
        self.ctx = ctx
        self._ops: list[OperationRecord] = []

        self.banner = Banner()
        self.banner.setVisible(False)
        self.body.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(section_title("Operations"), 1)
        self.undo_btn = QPushButton("Undo operation")
        self.undo_btn.setObjectName("Primary")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        head.addWidget(self.undo_btn)
        top_layout.addLayout(head)
        self.ops = QTableWidget(0, 6)
        self.ops.setHorizontalHeaderLabels(
            ["When", "Folder", "Status", "Moved", "Problems", "Size"]
        )
        self._style_table(self.ops)
        self.ops.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ops.itemSelectionChanged.connect(self._on_select)
        top_layout.addWidget(self.ops)
        splitter.addWidget(top)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.addWidget(section_title("Moves in this operation"))
        self.moves_hint = muted("Select an operation to see each file it moved.")
        bottom_layout.addWidget(self.moves_hint)
        self.moves = QTableWidget(0, 4)
        self.moves.setHorizontalHeaderLabels(["Status", "From", "To", "Details"])
        self._style_table(self.moves)
        self.moves.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.moves.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        bottom_layout.addWidget(self.moves)
        splitter.addWidget(bottom)
        splitter.setSizes([300, 400])
        self.body.addWidget(splitter, 1)

        ctx.history_changed.connect(self.refresh)
        ctx.busy_changed.connect(lambda _b: self._on_select())
        self.refresh()

    @staticmethod
    def _style_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def show_recovery(self, lines: list[str]) -> None:
        if not lines:
            return
        shown = "<br>".join(f"• {line}" for line in lines[:8])
        self.banner.set_text(
            "<b>Tidy was interrupted last time and has made everything consistent.</b><br>" + shown
        )
        self.banner.set_kind("warning")
        self.banner.setVisible(True)

    def refresh(self) -> None:
        selected = self._selected_op()
        self._ops = self.ctx.journal.operations()
        self.ops.setRowCount(len(self._ops))
        p = theme.current()
        for row, op in enumerate(self._ops):
            problems = op.failed + max(0, op.total - op.done - op.undone - op.failed)
            values = [
                _local(op.created_at),
                str(op.root),
                STATUS_LABELS.get(op.status, op.status),
                f"{op.done + op.undone:,} / {op.total:,}",
                f"{problems:,}" if problems else "",
                human_size(op.bytes_moved) if op.bytes_moved else "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, op.id)
                if col == 2 and op.status in (
                    OpStatus.PARTIAL,
                    OpStatus.PARTIALLY_UNDONE,
                    OpStatus.RECOVERED,
                ):
                    item.setForeground(QColor(p.warning))
                if col == 2 and op.status is OpStatus.UNDONE:
                    item.setForeground(QColor(p.muted))
                self.ops.setItem(row, col, item)
        if selected is not None:
            for row, op in enumerate(self._ops):
                if op.id == selected.id:
                    self.ops.selectRow(row)
                    break
        if not self._ops:
            self.moves.setRowCount(0)
        self._on_select()

    def _selected_op(self) -> OperationRecord | None:
        rows = self.ops.selectionModel().selectedRows() if self.ops.selectionModel() else []
        if not rows or not self._ops:
            return None
        row = rows[0].row()
        return self._ops[row] if row < len(self._ops) else None

    def _on_select(self) -> None:
        op = self._selected_op()
        self.undo_btn.setEnabled(op is not None and op.can_undo and not self.ctx.busy)
        if op is None:
            self.moves.setRowCount(0)
            self.moves_hint.setVisible(True)
            return
        self.moves_hint.setVisible(False)
        records = self.ctx.journal.moves(op.id)
        self.moves.setRowCount(len(records))
        p = theme.current()
        for row, rec in enumerate(records):
            to = rec.undo_path if rec.status is MoveState.UNDONE and rec.undo_path else None
            cells = [
                MOVE_LABELS.get(rec.status, rec.status),
                str(rec.source),
                str(rec.destination) + (f"  → restored to {to}" if to and to != rec.source else ""),
                rec.error or "",
            ]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if rec.status in (MoveState.FAILED, MoveState.UNDO_FAILED):
                    item.setForeground(QColor(p.danger))
                elif rec.status is MoveState.SKIPPED:
                    item.setForeground(QColor(p.warning))
                elif rec.status is MoveState.UNDONE:
                    item.setForeground(QColor(p.muted))
                self.moves.setItem(row, col, item)

    def _undo(self) -> None:
        op = self._selected_op()
        if op is None or not op.can_undo:
            return
        answer = QMessageBox.question(
            self,
            "Undo operation?",
            f"Move {op.done:,} items back to where they were in\n{op.root}?\n\n"
            "If something new now occupies an original location, the restored item is saved "
            "beside it with a number added — nothing is overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.undo_btn.setEnabled(False)
        self.ctx.undo(op.id, self._undo_done)

    def _undo_done(self, result: OperationResult | str) -> None:
        if isinstance(result, str):
            self.banner.set_text(f"<b>Undo stopped:</b> {result}")
            self.banner.set_kind("danger")
        else:
            text = f"<b>Restored {result.done:,} items.</b>"
            if result.failed:
                text += f" {result.failed:,} could not be restored (see details below)."
            if result.messages:
                text += "<br>" + "<br>".join(f"• {m}" for m in result.messages[:6])
            self.banner.set_text(text)
            self.banner.set_kind("warning" if result.failed else "info")
        self.banner.setVisible(True)
        self.refresh()
