"""Organisation Preview: review, edit, approve and execute the move plan."""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from tidy.core.history import OpStatus
from tidy.core.organiser import OperationResult
from tidy.models import MoveStatus
from tidy.ui.context import AppContext
from tidy.ui.models import Col, PlanFilterProxy, PlanModel
from tidy.ui.widgets import Banner, ConfidenceDelegate, Page, muted
from tidy.utils.formatting import human_size

Index = QModelIndex | QPersistentModelIndex


class CategoryDelegate(QStyledItemDelegate):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx

    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: Index) -> QWidget:
        combo = QComboBox(parent)
        for cat in self.ctx.categories.enabled():
            combo.addItem(f"{cat.label}  ·  {cat.folder}", cat.key)
        return combo

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        assert isinstance(editor, QComboBox)
        i = editor.findData(index.data(Qt.ItemDataRole.EditRole))
        editor.setCurrentIndex(max(i, 0))
        editor.showPopup()

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        assert isinstance(editor, QComboBox)
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class FolderDelegate(QStyledItemDelegate):
    def createEditor(self, parent: QWidget, option: QStyleOptionViewItem, index: Index) -> QWidget:
        edit = QLineEdit(parent)
        edit.setPlaceholderText("Folder inside the organised folder, e.g. Documents/Taxes")
        return edit

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        assert isinstance(editor, QLineEdit)
        editor.setText(index.data(Qt.ItemDataRole.EditRole) or "")

    def setModelData(self, editor: QWidget, model: QAbstractItemModel, index: Index) -> None:
        assert isinstance(editor, QLineEdit)
        model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)


class PreviewPage(Page):
    def __init__(self, ctx: AppContext, model: PlanModel) -> None:
        super().__init__(
            "Organisation Preview",
            "Every proposed move. Untick anything you want to leave alone, or double-click a "
            "category or destination to change it.",
        )
        self.ctx = ctx
        self.model = model
        self.proxy = PlanFilterProxy(self)
        self.proxy.setSourceModel(model)

        # --- toolbar ------------------------------------------------------ #
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.set_text)
        bar.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        for key, label in PlanFilterProxy.STATUS_FILTERS.items():
            self.status_filter.addItem(label, key)
        self.status_filter.currentIndexChanged.connect(
            lambda: self.proxy.set_status(self.status_filter.currentData())
        )
        bar.addWidget(self.status_filter)
        approve_all = QPushButton("Approve all")
        approve_all.clicked.connect(lambda: self._set_visible_approved(True))
        bar.addWidget(approve_all)
        reject_all = QPushButton("Reject all")
        reject_all.clicked.connect(lambda: self._set_visible_approved(False))
        bar.addWidget(reject_all)
        self.body.addLayout(bar)

        # --- table -------------------------------------------------------- #
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setItemDelegateForColumn(Col.CONFIDENCE, ConfidenceDelegate(self.table))
        self.table.setItemDelegateForColumn(Col.CATEGORY, CategoryDelegate(ctx, self.table))
        self.table.setItemDelegateForColumn(Col.DESTINATION, FolderDelegate(self.table))
        self.table.setColumnHidden(Col.MODIFIED, True)
        self.table.setColumnHidden(Col.LOCATION, True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(Col.APPROVE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(Col.APPROVE, 34)
        for col, width in (
            (Col.NAME, 260),
            (Col.CATEGORY, 180),
            (Col.CONFIDENCE, 120),
            (Col.DESTINATION, 230),
            (Col.SIZE, 80),
            (Col.WHY, 280),
        ):
            header.resizeSection(col, width)
        header.setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.body.addWidget(self.table, 1)

        # --- footer ------------------------------------------------------- #
        self.result_banner = Banner()
        self.result_banner.setVisible(False)
        self.body.addWidget(self.result_banner)

        footer = QHBoxLayout()
        self.summary = muted("")
        self.summary.setWordWrap(False)
        footer.addWidget(self.summary, 1)
        self.remember = QCheckBox("Remember my category changes")
        self.remember.setChecked(True)
        self.remember.setToolTip(
            "Save corrections as local rules so similar files are filed the same way next time."
        )
        footer.addWidget(self.remember)
        self.dry_run = QCheckBox("Dry run")
        self.dry_run.setChecked(True)
        self.dry_run.setToolTip("While dry run is on, nothing can be moved.")
        self.dry_run.toggled.connect(self._update_footer)
        footer.addWidget(self.dry_run)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        footer.addWidget(self.progress)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(ctx.cancel)
        footer.addWidget(self.cancel_btn)
        self.execute_btn = QPushButton("Execute plan")
        self.execute_btn.setObjectName("Primary")
        self.execute_btn.clicked.connect(self._execute)
        footer.addWidget(self.execute_btn)
        self.body.addLayout(footer)

        model.edited.connect(self._update_footer)
        model.modelReset.connect(self._update_footer)
        model.error.connect(lambda msg: QMessageBox.warning(self, "Can't use that folder", msg))
        ctx.busy_changed.connect(lambda _b: self._update_footer())
        ctx.execute_started.connect(self._on_execute_started)
        ctx.execute_progress.connect(self._on_execute_progress)
        ctx.execute_finished.connect(self._on_execute_finished)
        ctx.execute_failed.connect(self._on_execute_failed)
        ctx.scan_started.connect(lambda _r: self.result_banner.setVisible(False))
        self._update_footer()

    # ------------------------------------------------------------------ #
    def _selected_rows(self) -> list[int]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return sorted(rows)

    def _visible_rows(self) -> list[int]:
        return [
            self.proxy.mapToSource(self.proxy.index(r, 0)).row()
            for r in range(self.proxy.rowCount())
        ]

    def _set_visible_approved(self, approved: bool) -> None:
        self.model.set_approved(self._visible_rows(), approved)

    def _context_menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        rows = self._selected_rows()
        if not rows:
            return
        menu = QMenu(self)
        menu.addAction("Approve selected", lambda: self.model.set_approved(rows, True))
        menu.addAction("Reject selected", lambda: self.model.set_approved(rows, False))
        sub = menu.addMenu("Move selected to category")
        for cat in self.ctx.categories.enabled():
            sub.addAction(
                f"{cat.label}  ·  {cat.folder}",
                lambda key=cat.key: self.model.set_category(rows, key),
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _update_footer(self) -> None:
        plan = self.model.plan
        if plan is None:
            self.summary.setText("No plan yet — scan a folder from the Dashboard.")
            self.execute_btn.setEnabled(False)
            self.execute_btn.setText("Execute plan")
            return
        moves = plan.actionable()
        size = sum(m.item.size for m in moves)
        unchanged = sum(1 for m in plan.moves if m.status is MoveStatus.UNCHANGED)
        rejected = sum(1 for m in plan.moves if not m.approved and not m.is_noop)
        parts = [f"<b>{len(moves):,}</b> moves approved ({human_size(size)})"]
        if rejected:
            parts.append(f"{rejected:,} not approved")
        if unchanged:
            parts.append(f"{unchanged:,} already in place")
        self.summary.setText(" · ".join(parts))
        dry = self.dry_run.isChecked()
        self.execute_btn.setText(
            "Dry run on — untick to execute" if dry else f"Execute {len(moves):,} moves"
        )
        self.execute_btn.setEnabled(bool(moves) and not dry and not self.ctx.busy)

    def _execute(self) -> None:
        plan = self.model.plan
        if plan is None or self.dry_run.isChecked():
            return
        moves = plan.actionable()
        if not moves:
            return
        size = sum(m.item.size for m in moves)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Move files?")
        box.setText(f"Move {len(moves):,} items ({human_size(size)}) inside\n{plan.root}?")
        box.setInformativeText(
            "Nothing will be deleted or overwritten. Every move is recorded, and you can undo "
            "the whole operation from the History page."
        )
        go = box.addButton("Move files", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        self.ctx.execute(moves, remember_corrections=self.remember.isChecked())

    def _on_execute_started(self, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.result_banner.setVisible(False)

    def _on_execute_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.summary.setText(f"Moving {done + 1 if done < total else done:,} of {total:,} — {name}")

    def _on_execute_finished(self, result: OperationResult) -> None:
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.dry_run.setChecked(True)  # always fall back to the safe state
        verb = {OpStatus.COMPLETED: "Done", OpStatus.CANCELLED: "Stopped"}.get(
            result.status, "Finished with issues"
        )
        lines = [f"<b>{verb}.</b> Moved {result.done:,} items"]
        if result.failed:
            lines.append(f"{result.failed:,} failed")
        if result.skipped:
            lines.append(f"{result.skipped:,} skipped")
        text = ", ".join(lines) + ". You can undo this from History."
        if result.messages:
            text += "<br>" + "<br>".join(f"• {m}" for m in result.messages[:6])
            if len(result.messages) > 6:
                text += f"<br>…and {len(result.messages) - 6} more (see History)."
        self.result_banner.set_text(text)
        self.result_banner.set_kind("info" if result.status is OpStatus.COMPLETED else "warning")
        self.result_banner.setVisible(True)

    def _on_execute_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.dry_run.setChecked(True)
        self.result_banner.set_text(
            f"<b>The operation stopped:</b> {message}. Check History for what was completed."
        )
        self.result_banner.set_kind("danger")
        self.result_banner.setVisible(True)
