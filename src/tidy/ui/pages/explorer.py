"""File Explorer: browse everything the scan discovered."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableView,
)

from tidy.ui.context import AppContext
from tidy.ui.models import Col, PlanFilterProxy, PlanModel
from tidy.ui.widgets import ConfidenceDelegate, Page


def reveal_in_file_manager(path: str) -> None:
    """Open the system file manager with ``path`` selected (read-only action)."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class ExplorerPage(Page):
    def __init__(self, ctx: AppContext, model: PlanModel) -> None:
        super().__init__("File Explorer", "Everything Tidy found, with where it would go.")
        self.ctx = ctx
        self.model = model
        self.proxy = PlanFilterProxy(self)
        self.proxy.setSourceModel(model)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, folders, categories…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.set_text)
        bar.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.setMinimumWidth(200)
        self.category.currentIndexChanged.connect(
            lambda: self.proxy.set_category(self.category.currentData())
        )
        bar.addWidget(self.category)
        self.body.addLayout(bar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setItemDelegateForColumn(Col.CONFIDENCE, ConfidenceDelegate(self.table))
        for col in (Col.APPROVE, Col.WHY, Col.NOTE):
            self.table.setColumnHidden(col, True)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(Col.NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in (
            (Col.LOCATION, 160),
            (Col.CATEGORY, 180),
            (Col.CONFIDENCE, 120),
            (Col.DESTINATION, 240),
            (Col.SIZE, 90),
            (Col.MODIFIED, 130),
        ):
            header.resizeSection(col, width)
        self.table.doubleClicked.connect(self._reveal)
        self.table.setToolTip("Double-click to show the file in your file manager")
        self.body.addWidget(self.table, 1)

        ctx.settings_changed.connect(self._refresh_categories)
        model.modelReset.connect(self._refresh_categories)
        self._refresh_categories()

    def _refresh_categories(self) -> None:
        current = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", None)
        for cat in self.ctx.categories.enabled():
            self.category.addItem(f"{cat.label}  ·  {cat.folder}", cat.key)
        index = self.category.findData(current)
        self.category.setCurrentIndex(max(index, 0))
        self.category.blockSignals(False)
        self.proxy.set_category(self.category.currentData())
        count = self.model.rowCount()
        self.set_subtitle(
            f"{count:,} items in {self.ctx.root}"
            if count
            else "Scan a folder from the Dashboard to see its files here."
        )

    def _reveal(self, index: QModelIndex) -> None:
        source = self.proxy.mapToSource(index)
        move = self.model.move_at(source.row())
        if move.item.path.exists():
            reveal_in_file_manager(str(move.item.path))
