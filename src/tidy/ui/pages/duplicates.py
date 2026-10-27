"""Duplicates: exact copies (by SHA-256) and look-alike names. Never deletes."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tidy.core.duplicates import DuplicateFinder, DuplicateGroup, DuplicateReport, FileRef
from tidy.ui import theme
from tidy.ui.context import AppContext
from tidy.ui.pages.explorer import reveal_in_file_manager
from tidy.ui.widgets import Banner, Card, Page, StatCard, muted, section_title
from tidy.utils.formatting import human_size, human_time


def _rel(ctx: AppContext, ref: FileRef) -> str:
    try:
        return ref.path.relative_to(ctx.root).as_posix()
    except ValueError:
        return str(ref.path)


class DuplicatesPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            "Duplicates",
            "Identical files found by comparing their contents (SHA-256). Tidy never deletes "
            "anything — use this to decide what you want to remove yourself.",
        )
        self.ctx = ctx
        self.report: DuplicateReport | None = None
        self._worker = None

        self.find_btn = QPushButton("Find duplicates")
        self.find_btn.setObjectName("Primary")
        self.find_btn.clicked.connect(self.run)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.header_actions.addWidget(self.cancel_btn)
        self.header_actions.addWidget(self.find_btn)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_groups = StatCard("Duplicate groups")
        self.stat_copies = StatCard("Extra copies")
        self.stat_saving = StatCard("Potential space savings")
        self.stat_checked = StatCard("Files compared")
        for card in (self.stat_groups, self.stat_copies, self.stat_saving, self.stat_checked):
            stats.addWidget(card, 1)
        self.body.addLayout(stats)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        self.body.addWidget(self.progress)
        self.status = muted("Searches every file inside the chosen folder, including sub-folders.")
        self.body.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # -- identical -------------------------------------------------------
        identical = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Group / file", "Size", "Modified"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(self._reveal_item)
        self.tree.currentItemChanged.connect(self._show_comparison)
        identical.addWidget(self.tree)

        compare = Card()
        compare.layout_.addWidget(section_title("Comparison"))
        self.compare_hint = muted("Select a group to compare its files side by side.")
        compare.layout_.addWidget(self.compare_hint)
        self.compare = QTableWidget(0, 0)
        self.compare.verticalHeader().setDefaultSectionSize(28)
        self.compare.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.compare.setShowGrid(False)
        self.compare.setWordWrap(True)
        compare.layout_.addWidget(self.compare, 1)
        identical.addWidget(compare)
        identical.setSizes([620, 520])
        self.tabs.addTab(identical, "Identical files")

        # -- similar names ---------------------------------------------------
        similar = QWidget()
        sl = QVBoxLayout(similar)
        sl.setContentsMargins(0, 8, 0, 0)
        sl.addWidget(
            muted(
                "These look like copies by name but their contents differ, so they "
                "are probably different versions. They are not duplicates."
            )
        )
        self.similar = QTreeWidget()
        self.similar.setHeaderLabels(["Name", "Size", "Modified"])
        self.similar.setAlternatingRowColors(True)
        self.similar.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.similar.itemDoubleClicked.connect(self._reveal_item)
        sl.addWidget(self.similar)
        self.tabs.addTab(similar, "Similar names")
        self.body.addWidget(self.tabs, 1)

        self.errors = Banner(kind="warning")
        self.errors.setVisible(False)
        self.body.addWidget(self.errors)

        ctx.scan_finished.connect(lambda _r: self.run())
        ctx.busy_changed.connect(lambda busy: self.find_btn.setEnabled(not busy))
        ctx.settings_changed.connect(self.clear)

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        if self._worker is not None or not self.ctx.root.is_dir():
            return
        root = self.ctx.root
        db = self.ctx.db

        def job(cancel, emit):
            finder = DuplicateFinder(db, cancel, lambda *a: emit(a))
            return finder.find(root)

        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.find_btn.setEnabled(False)
        self.status.setText(f"Comparing files in {root}…")
        self._worker = self.ctx.run_background(job, self._done, self._failed, self._progress)
        self._worker.signals.cancelled.connect(self._cancelled)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel.set()

    def _progress(self, args: tuple[str, int, int]) -> None:
        phase, done, total = args
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        label = "Quick check" if phase == "partial" else "Verifying with SHA-256"
        self.status.setText(f"{label} — {done:,} of {total:,} candidate files")

    def _finish(self) -> None:
        self._worker = None
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.find_btn.setEnabled(not self.ctx.busy)

    def _cancelled(self) -> None:
        self._finish()
        self.status.setText("Duplicate search cancelled.")

    def _failed(self, message: str) -> None:
        self._finish()
        self.status.setText(f"Duplicate search failed: {message}")

    def clear(self) -> None:
        self.report = None
        self.tree.clear()
        self.similar.clear()
        self.compare.setRowCount(0)
        for card in (self.stat_groups, self.stat_copies, self.stat_saving, self.stat_checked):
            card.set("—")
        self.ctx.extra_results.emit("duplicates", None)

    def _done(self, report: DuplicateReport) -> None:
        self._finish()
        self.report = report
        self.ctx.extra_results.emit("duplicates", report)
        copies = sum(len(g.files) - 1 for g in report.groups)
        self.stat_groups.set(f"{len(report.groups):,}")
        self.stat_copies.set(f"{copies:,}")
        self.stat_saving.set(
            human_size(report.wasted_bytes), "if you kept one copy of each" if report.groups else ""
        )
        self.stat_checked.set(f"{report.files_checked:,}")
        self.status.setText(
            f"Found {len(report.groups):,} groups of identical files in {report.root}."
            if report.groups
            else f"No identical files in {report.root}."
        )
        if report.errors:
            self.errors.set_text(
                f"{len(report.errors)} file(s) could not be read (in use or "
                "access denied) and were not compared."
            )
            self.errors.setVisible(True)
        else:
            self.errors.setVisible(False)
        self._fill_tree(report)

    def _fill_tree(self, report: DuplicateReport) -> None:
        p = theme.current()
        self.tree.clear()
        for index, group in enumerate(report.groups, 1):
            top = QTreeWidgetItem(
                [
                    f"{group.files[0].path.name}  —  {len(group.files)} identical copies",
                    human_size(group.size),
                    f"{human_size(group.wasted)} reclaimable",
                ]
            )
            top.setData(0, Qt.ItemDataRole.UserRole, index - 1)
            top.setForeground(2, QColor(p.warning))
            for n, ref in enumerate(group.files):
                label = _rel(self.ctx, ref) + ("   (oldest)" if n == 0 else "")
                child = QTreeWidgetItem(
                    [label, human_size(ref.size), human_time(ref.mtime_ns / 1e9)]
                )
                child.setData(0, Qt.ItemDataRole.UserRole + 1, str(ref.path))
                child.setToolTip(0, str(ref.path))
                top.addChild(child)
            self.tree.addTopLevelItem(top)
        if report.groups:
            self.tree.topLevelItem(0).setExpanded(True)
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.similar.clear()
        for members in report.similar_names:
            top = QTreeWidgetItem([f"{members[0].path.stem}…  —  {len(members)} versions", "", ""])
            for ref in members:
                child = QTreeWidgetItem(
                    [_rel(self.ctx, ref), human_size(ref.size), human_time(ref.mtime_ns / 1e9)]
                )
                child.setData(0, Qt.ItemDataRole.UserRole + 1, str(ref.path))
                top.addChild(child)
            self.similar.addTopLevelItem(top)
        self.tabs.setTabText(1, f"Similar names ({len(report.similar_names)})")
        self.tabs.setTabText(0, f"Identical files ({len(report.groups)})")

    def _group_of(self, item: QTreeWidgetItem | None) -> DuplicateGroup | None:
        if item is None or self.report is None:
            return None
        top = item if item.parent() is None else item.parent()
        index = top.data(0, Qt.ItemDataRole.UserRole)
        return self.report.groups[index] if isinstance(index, int) else None

    def _show_comparison(self, item: QTreeWidgetItem | None, _prev=None) -> None:  # type: ignore[no-untyped-def]
        group = self._group_of(item)
        if group is None:
            self.compare.setRowCount(0)
            self.compare.setColumnCount(0)
            self.compare_hint.setVisible(True)
            return
        files = group.files
        self.compare_hint.setText(
            f"All {len(files)} copies are byte-for-byte identical "
            f"({group.size:,} bytes, SHA-256 {group.sha256[:12]}…). The oldest is listed first."
        )
        self.compare_hint.setVisible(True)
        headers = ["Location", "Modified", "Created"]
        self.compare.setColumnCount(len(headers))
        self.compare.setHorizontalHeaderLabels(headers)
        self.compare.setRowCount(len(files))
        self.compare.verticalHeader().setVisible(False)
        for row, ref in enumerate(files):
            try:
                st = os.stat(ref.path)
                created = getattr(st, "st_birthtime", None) or st.st_ctime
                created_text = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M")
            except OSError:
                created_text = "unavailable"
            values = [_rel(self.ctx, ref), human_time(ref.mtime_ns / 1e9), created_text]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(ref.path))
                self.compare.setItem(row, col, cell)
        header = self.compare.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def _reveal_item(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if path and os.path.exists(path):
            reveal_in_file_manager(path)

    def _menu(self, pos) -> None:  # type: ignore[no-untyped-def]
        item = self.tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not path:
            return
        menu = QMenu(self)
        menu.addAction("Show in folder", lambda: reveal_in_file_manager(path))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText(path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
