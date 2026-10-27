"""Qt item models over the engine's plan."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QFileInfo,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFileIconProvider

from tidy.core.categories import REVIEW
from tidy.core.planner import Planner
from tidy.models import MoveStatus, Plan, PlannedMove, Source
from tidy.ui import theme
from tidy.utils.formatting import human_size, human_time
from tidy.utils.paths import UnsafePathError

Index = QModelIndex | QPersistentModelIndex


class Col:
    APPROVE, NAME, LOCATION, CATEGORY, CONFIDENCE, DESTINATION, SIZE, MODIFIED, WHY, NOTE = range(
        10
    )


HEADERS = ["", "Name", "Location", "Category", "Confidence", "Proposed destination", "Size",
           "Modified", "Why", "Notes"]  # fmt: skip


class _IconCache:
    def __init__(self) -> None:
        self._provider: QFileIconProvider | None = None
        self._cache: dict[str, QIcon] = {}

    def icon(self, move: PlannedMove) -> QIcon:
        if self._provider is None:
            self._provider = QFileIconProvider()
        if move.item.is_folder:
            key = "<folder>"
            if key not in self._cache:
                self._cache[key] = self._provider.icon(QFileIconProvider.IconType.Folder)
            return self._cache[key]
        key = move.item.extension or "<none>"
        if key not in self._cache:
            # A generic icon per extension; avoids hitting the shell for every file.
            self._cache[key] = self._provider.icon(QFileInfo(f"x{move.item.extension}"))
        return self._cache[key]


class PlanModel(QAbstractTableModel):
    """One shared model for the Explorer and Preview pages."""

    error = Signal(str)
    edited = Signal()

    def __init__(self, planner_factory, label_for, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._plan: Plan | None = None
        self._planner_factory = planner_factory
        self._label_for = label_for
        self._icons = _IconCache()

    # -- plan ------------------------------------------------------------ #
    @property
    def plan(self) -> Plan | None:
        return self._plan

    def set_plan(self, plan: Plan | None) -> None:
        self.beginResetModel()
        self._plan = plan
        self.endResetModel()

    def move_at(self, row: int) -> PlannedMove:
        assert self._plan is not None
        return self._plan.moves[row]

    def _planner(self) -> Planner:
        return self._planner_factory()

    # -- Qt model API --------------------------------------------------- #
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() or self._plan is None else len(self._plan.moves)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return len(HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = 0) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not index.isValid() or self._plan is None:
            return base
        move = self.move_at(index.row())
        if move.status is MoveStatus.UNCHANGED:
            return base
        if index.column() == Col.APPROVE and move.status is MoveStatus.PROPOSED:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in (Col.CATEGORY, Col.DESTINATION):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or self._plan is None:
            return None
        move = self.move_at(index.row())
        col = index.column()
        item = move.item
        cls = move.classification
        root = self._plan.root

        if role == Qt.ItemDataRole.CheckStateRole and col == Col.APPROVE:
            if move.status is not MoveStatus.PROPOSED:
                return None
            return Qt.CheckState.Checked if move.approved else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == Col.NAME:
                suffix = f"  ({item.file_count} files)" if item.is_folder else ""
                return item.name + suffix
            if col == Col.LOCATION:
                parent = item.path.parent.relative_to(root)
                return "(top level)" if parent == Path() else parent.as_posix()
            if col == Col.CATEGORY:
                return self._label_for(cls.category)
            if col == Col.DESTINATION:
                if move.status is MoveStatus.UNCHANGED:
                    return "— stays where it is"
                if move.status is MoveStatus.SKIPPED:
                    return "— skipped"
                rel = move.destination.relative_to(root)
                if move.destination.name != item.name:
                    return rel.as_posix()
                return rel.parent.as_posix() + "/"
            if col == Col.SIZE:
                return human_size(item.size)
            if col == Col.MODIFIED:
                return human_time(item.mtime_ns / 1e9)
            if col == Col.WHY:
                return cls.explain()
            if col == Col.NOTE:
                return move.note
            return None

        if role == Qt.ItemDataRole.EditRole:
            if col == Col.CATEGORY:
                return cls.category
            if col == Col.DESTINATION:
                return move.destination.parent.relative_to(root).as_posix()
            return None

        if role == Qt.ItemDataRole.UserRole:  # sort keys / raw values
            if col == Col.CONFIDENCE:
                return float(cls.confidence)
            if col == Col.SIZE:
                return item.size
            if col == Col.MODIFIED:
                return item.mtime_ns
            if col == Col.APPROVE:
                return int(move.approved)
            return (self.data(index) or "").casefold()

        if role == Qt.ItemDataRole.ToolTipRole:
            if col == Col.NAME:
                return str(item.path)
            if col == Col.DESTINATION and move.status is MoveStatus.PROPOSED:
                return str(move.destination)
            if col in (Col.WHY, Col.CATEGORY, Col.CONFIDENCE):
                return f"{self._label_for(cls.category)} — {cls.source.value}\n" + "\n".join(
                    f"• {r}" for r in cls.reasons
                )
            if col == Col.NOTE:
                return move.note or None
            return None

        if role == Qt.ItemDataRole.DecorationRole and col == Col.NAME:
            return self._icons.icon(move)

        if role == Qt.ItemDataRole.ForegroundRole:
            p = theme.current()
            if move.status is not MoveStatus.PROPOSED or not move.approved:
                return QColor(p.muted)
            if col == Col.CATEGORY and cls.category == REVIEW:
                return QColor(p.warning)
            if col == Col.CATEGORY and cls.source is Source.USER:
                return QColor(p.accent)
            if col in (Col.NOTE, Col.WHY, Col.LOCATION, Col.MODIFIED, Col.SIZE):
                return QColor(p.muted)
        return None

    def setData(self, index: Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or self._plan is None:
            return False
        move = self.move_at(index.row())
        planner = self._planner()
        try:
            if index.column() == Col.APPROVE and role == Qt.ItemDataRole.CheckStateRole:
                move.approved = Qt.CheckState(value) == Qt.CheckState.Checked
                planner.recompute(self._plan)
            elif index.column() == Col.CATEGORY and role == Qt.ItemDataRole.EditRole:
                if value == move.classification.category:
                    return False
                planner.set_category(self._plan, move, str(value))
            elif index.column() == Col.DESTINATION and role == Qt.ItemDataRole.EditRole:
                current = move.destination.parent.relative_to(self._plan.root).as_posix()
                if str(value).strip().replace("\\", "/").strip("/") == current:
                    return False
                planner.set_custom_folder(self._plan, move, str(value))
            else:
                return False
        except (UnsafePathError, ValueError) as exc:
            self.error.emit(str(exc))
            return False
        self._refresh_all()
        return True

    def set_approved(self, rows: list[int], approved: bool) -> None:
        if self._plan is None:
            return
        for row in rows:
            move = self._plan.moves[row]
            if move.status is MoveStatus.PROPOSED:
                move.approved = approved
        self._planner().recompute(self._plan)
        self._refresh_all()

    def set_category(self, rows: list[int], category: str) -> None:
        if self._plan is None:
            return
        planner = self._planner()
        for row in rows:
            move = self._plan.moves[row]
            if move.status is not MoveStatus.UNCHANGED:
                planner.set_category(self._plan, move, category)
        self._refresh_all()

    def _refresh_all(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(
                self.index(0, 0), self.index(self.rowCount() - 1, len(HEADERS) - 1)
            )
        self.edited.emit()


class PlanFilterProxy(QSortFilterProxyModel):
    STATUS_FILTERS = {
        "all": "All items",
        "approved": "Approved",
        "rejected": "Not approved",
        "review": "Needs review",
        "low": "Low confidence (<70%)",
        "edited": "Edited by you",
        "groups": "Related groups",
    }

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._text = ""
        self._category: str | None = None
        self._status = "all"
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setDynamicSortFilter(False)

    def _refilter(self, change) -> None:  # type: ignore[no-untyped-def]
        # Qt >= 6.10 replaces invalidateFilter() with begin/endFilterChange().
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            change()
            self.endFilterChange()
        else:  # pragma: no cover - older Qt
            change()
            self.invalidateFilter()

    def set_text(self, text: str) -> None:
        self._refilter(lambda: setattr(self, "_text", text.casefold().strip()))

    def set_category(self, key: str | None) -> None:
        self._refilter(lambda: setattr(self, "_category", key))

    def set_status(self, status: str) -> None:
        self._refilter(lambda: setattr(self, "_status", status))

    def filterAcceptsRow(self, row: int, parent: Index) -> bool:
        model = self.sourceModel()
        if not isinstance(model, PlanModel) or model.plan is None:
            return True
        move = model.move_at(row)
        cls = move.classification
        if self._category and cls.category != self._category:
            return False
        status = self._status
        if status == "approved" and not (move.approved and not move.is_noop):
            return False
        if status == "rejected" and move.approved:
            return False
        if status == "review" and cls.category != REVIEW:
            return False
        if status == "low" and cls.confidence >= 0.7:
            return False
        if status == "edited" and not move.edited:
            return False
        if status == "groups" and not move.group_id:
            return False
        if self._text:
            hay = " ".join(
                [
                    move.item.name,
                    os.fspath(move.item.rel_path),
                    cls.category,
                    os.fspath(move.destination),
                    cls.explain(),
                ]
            ).casefold()
            return self._text in hay
        return True
