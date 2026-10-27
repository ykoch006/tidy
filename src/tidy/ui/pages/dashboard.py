"""Dashboard: choose a folder, scan it, and see what Tidy found."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from tidy.core.categories import REVIEW
from tidy.core.pipeline import PipelineResult
from tidy.ui.context import AppContext
from tidy.ui.widgets import Banner, Card, Page, StatCard, muted, section_title
from tidy.utils.formatting import human_size

PHASES = {"scan": "Scanning", "classify": "Classifying", "ai": "Asking AI", "plan": "Planning"}


class DashboardPage(Page):
    review_requested = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Dashboard", "Scan a folder to see how Tidy would organise it.")
        self.ctx = ctx

        # --- folder + scan card ------------------------------------------ #
        folder_card = Card()
        row = QHBoxLayout()
        row.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(muted("Folder to organise"))
        self.folder_label = QLabel()
        self.folder_label.setObjectName("SectionTitle")
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.folder_label)
        row.addLayout(col, 1)
        self.change_btn = QPushButton("Change…")
        self.change_btn.clicked.connect(self._choose_folder)
        row.addWidget(self.change_btn)
        self.scan_btn = QPushButton("Scan folder")
        self.scan_btn.setObjectName("Primary")
        self.scan_btn.clicked.connect(self.ctx.start_scan)
        row.addWidget(self.scan_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.ctx.cancel)
        self.cancel_btn.setVisible(False)
        row.addWidget(self.cancel_btn)
        folder_card.layout_.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        folder_card.layout_.addWidget(self.progress)
        self.progress_label = muted("")
        self.progress_label.setVisible(False)
        folder_card.layout_.addWidget(self.progress_label)
        self.body.addWidget(folder_card)

        self.safety = Banner(
            "<b>Dry run is on.</b> Scanning only reads your files. Nothing is moved until you "
            "review the plan and press <b>Execute</b> on the Organisation Preview page."
        )
        self.body.addWidget(self.safety)

        # --- stats -------------------------------------------------------- #
        stats = QGridLayout()
        stats.setSpacing(12)
        self.stat_files = StatCard("Items scanned")
        self.stat_size = StatCard("Storage analysed")
        self.stat_categories = StatCard("Categories found")
        self.stat_duplicates = StatCard("Potential duplicates", hint="Run on the Duplicates page")
        self.stat_review = StatCard("Need your review")
        for i, card in enumerate(
            [
                self.stat_files,
                self.stat_size,
                self.stat_categories,
                self.stat_duplicates,
                self.stat_review,
            ]
        ):
            stats.addWidget(card, 0, i)
            stats.setColumnStretch(i, 1)
        self.body.addLayout(stats)

        # --- breakdown ---------------------------------------------------- #
        breakdown = Card()
        head = QHBoxLayout()
        head.addWidget(section_title("Proposed organisation"), 1)
        self.skipped_btn = QPushButton("Skipped items")
        self.skipped_btn.clicked.connect(self._show_skipped)
        self.skipped_btn.setEnabled(False)
        head.addWidget(self.skipped_btn)
        self.review_btn = QPushButton("Review plan →")
        self.review_btn.setObjectName("Primary")
        self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(self.review_requested.emit)
        head.addWidget(self.review_btn)
        breakdown.layout_.addLayout(head)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Destination folder", "Items", "Size", "Share"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        breakdown.layout_.addWidget(self.table, 1)
        self.body.addWidget(breakdown, 1)

        ctx.scan_started.connect(self._on_scan_started)
        ctx.scan_progress.connect(self._on_progress)
        ctx.scan_finished.connect(self._on_finished)
        ctx.scan_failed.connect(self._on_failed)
        ctx.scan_cancelled.connect(self._on_cancelled)
        ctx.busy_changed.connect(self._on_busy)
        ctx.plan_changed.connect(self._refresh_from_ctx)
        ctx.settings_changed.connect(self._refresh_folder)
        ctx.extra_results.connect(self._on_extra)
        self._refresh_folder()

    # ------------------------------------------------------------------ #
    def _refresh_folder(self) -> None:
        root = self.ctx.root
        exists = root.is_dir()
        self.folder_label.setText(str(root) if exists else f"{root}  (not found)")
        self.scan_btn.setEnabled(exists and not self.ctx.busy)

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a folder to organise", str(self.ctx.root)
        )
        if chosen:
            self.ctx.set_root(Path(chosen))
            self._refresh_folder()
            self._clear_stats()

    def _on_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy and self.ctx.root.is_dir())
        self.change_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)

    def _on_scan_started(self, root: str) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress_label.setVisible(True)
        self.progress_label.setText("Starting scan…")
        self._clear_stats()

    def _on_progress(self, phase: str, done: int, total: int, message: str) -> None:
        name = PHASES.get(phase, phase.title())
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress_label.setText(f"{name} — {done:,} of {total:,}")
        else:
            self.progress.setRange(0, 0)
            detail = f" — {message}" if message else ""
            self.progress_label.setText(f"{name} — {done:,} items{detail}")

    def _on_finished(self, result: PipelineResult) -> None:
        self.progress.setVisible(False)
        self.progress_label.setText(f"Scan complete: {len(result.items):,} items in {result.root}")

    def _on_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.progress_label.setText(f"Scan failed: {message}")

    def _on_cancelled(self) -> None:
        self.progress.setVisible(False)
        self.progress_label.setText("Scan cancelled.")

    def _on_extra(self, kind: str, payload: object) -> None:
        if kind == "duplicates" and payload is not None:
            groups = getattr(payload, "groups", [])
            wasted = getattr(payload, "wasted_bytes", 0)
            self.stat_duplicates.set(
                f"{sum(len(g.files) - 1 for g in groups):,}",
                f"{human_size(wasted)} reclaimable" if groups else "No exact duplicates",
            )

    def _clear_stats(self) -> None:
        for card in (self.stat_files, self.stat_size, self.stat_categories, self.stat_review):
            card.set("—")
        self.table.setRowCount(0)
        self.review_btn.setEnabled(False)
        self.skipped_btn.setEnabled(False)

    def _refresh_from_ctx(self) -> None:
        result = self.ctx.result
        if result is None:
            self._clear_stats()
            return
        plan = result.plan
        self.stat_files.set(
            f"{len(result.items):,}",
            f"{result.stats.files:,} files · {result.stats.folders:,} folders",
        )
        self.stat_size.set(human_size(plan.total_size))
        counts: Counter[str] = Counter()
        sizes: dict[str, int] = defaultdict(int)
        for move in plan.moves:
            if move.is_noop:
                continue
            folder = self.ctx.categories.resolve(move.classification.category).folder
            if move.custom_folder is not None:
                folder = move.custom_folder.as_posix()
            counts[folder] += 1
            sizes[folder] += move.item.size
        categories = {m.classification.category for m in plan.moves}
        self.stat_categories.set(str(len(categories - {REVIEW})))
        review = sum(1 for m in plan.moves if m.classification.category == REVIEW)
        self.stat_review.set(str(review), "Placed in Review/" if review else "All classified")
        total = sum(counts.values()) or 1
        self.table.setRowCount(len(counts))
        for row, (folder, count) in enumerate(counts.most_common()):
            self.table.setItem(row, 0, QTableWidgetItem(folder + "/"))
            self.table.setItem(row, 1, _right(f"{count:,}"))
            self.table.setItem(row, 2, _right(human_size(sizes[folder])))
            self.table.setItem(row, 3, _right(f"{count / total:.0%}"))
        self.review_btn.setEnabled(bool(counts))
        skipped = len(result.stats.skipped)
        self.skipped_btn.setText(f"Skipped items ({skipped})")
        self.skipped_btn.setEnabled(skipped > 0)

    def _show_skipped(self) -> None:
        if self.ctx.result is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Items Tidy did not include")
        dialog.resize(760, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(muted("These were skipped for safety and will not be touched."))
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Path", "Reason"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        skipped = self.ctx.result.stats.skipped
        table.setRowCount(len(skipped))
        for row, entry in enumerate(skipped):
            table.setItem(row, 0, QTableWidgetItem(str(entry.path)))
            table.setItem(row, 1, QTableWidgetItem(entry.reason))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()


def _right(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item
