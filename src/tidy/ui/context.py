"""Application state shared by all pages, and the bridge to the core engine."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from tidy.core.categories import CategoryTree
from tidy.core.history import Journal, OpStatus
from tidy.core.learning import LearnedRules
from tidy.core.organiser import OperationResult, Organiser, RecoveryReport
from tidy.core.pipeline import PipelineConfig, PipelineResult, run_pipeline
from tidy.core.planner import Planner
from tidy.database import Database
from tidy.models import PlannedMove, Source
from tidy.settings import AppSettings
from tidy.ui.workers import TaskRunner, Worker
from tidy.utils.logging import get_logger
from tidy.utils.paths import default_downloads_dir

log = get_logger(__name__)


class AppContext(QObject):
    # Scan lifecycle
    scan_started = Signal(str)
    scan_progress = Signal(str, int, int, str)  # phase, done, total, message
    scan_finished = Signal(object)  # PipelineResult
    scan_failed = Signal(str)
    scan_cancelled = Signal()
    # Plan / execution
    plan_changed = Signal()
    execute_started = Signal(int)
    execute_progress = Signal(int, int, str)
    execute_finished = Signal(object)  # OperationResult
    execute_failed = Signal(str)
    history_changed = Signal()
    settings_changed = Signal()
    busy_changed = Signal(bool)
    # Duplicates and other analysis hooks are added by their own modules.
    extra_results = Signal(str, object)

    def __init__(self, db: Database, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.settings = AppSettings.load(db)
        self.journal = Journal(db)
        self.learned = LearnedRules(db)
        self.categories = self._build_categories()
        self.runner = TaskRunner(self)
        self.result: PipelineResult | None = None
        self.root: Path = self._initial_root()
        self._busy = False
        self._scan_worker: Worker | None = None
        self._exec_worker: Worker | None = None
        # Optional layers, installed by later modules (content analysis, AI).
        self.content_layer_factory: Callable[[AppSettings], Any] | None = None
        self.post_classify_factory: Callable[[AppContext], Any] | None = None
        self.last_ai_summary: str = ""

    # ------------------------------------------------------------------ #
    def _initial_root(self) -> Path:
        if self.settings.downloads_dir:
            return Path(self.settings.downloads_dir)
        return default_downloads_dir()

    def _build_categories(self) -> CategoryTree:
        return CategoryTree(self.settings.category_overrides, self.settings.custom_categories)

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)

    @property
    def planner(self) -> Planner:
        return Planner(self.categories, self.settings.confidence_threshold)

    def save_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        settings.save(self.db)
        self.categories = self._build_categories()
        self.learned.invalidate()
        if settings.downloads_dir:
            self.root = Path(settings.downloads_dir)
        self.settings_changed.emit()

    def set_root(self, root: Path) -> None:
        self.root = root
        self.result = None
        self.plan_changed.emit()

    # ------------------------------------------------------------------ #
    # Startup recovery
    # ------------------------------------------------------------------ #
    def recover(self) -> RecoveryReport:
        report = Organiser(self.journal).recover()
        if report.needed:
            self.history_changed.emit()
        return report

    # ------------------------------------------------------------------ #
    # Scanning (dry run — never modifies the disk)
    # ------------------------------------------------------------------ #
    def start_scan(self) -> None:
        if self._busy:
            return
        root = self.root
        settings = self.settings
        config = PipelineConfig(
            categories=self.categories,
            threshold=settings.confidence_threshold,
            include_subdirectories=settings.include_subdirectories,
            include_hidden=settings.include_hidden,
            excluded_dirs=frozenset(self.journal.all_created_dirs()),
        )
        content = (
            self.content_layer_factory(settings)
            if self.content_layer_factory and settings.analyse_contents
            else None
        )
        post = self.post_classify_factory(self) if self.post_classify_factory else None

        def job(cancel, emit):  # runs on a worker thread
            return run_pipeline(
                root,
                config,
                learned=self.learned,
                content=content,
                cancel=cancel,
                progress=lambda *a: emit(a),
                post_classify=(lambda items, cls: post(items, cls, cancel, emit)) if post else None,
            )

        worker = Worker(job)
        worker.signals.progress.connect(lambda a: self.scan_progress.emit(*a))
        worker.signals.finished.connect(self._scan_done)
        worker.signals.failed.connect(self._scan_failed)
        worker.signals.cancelled.connect(self._scan_cancelled)
        self._scan_worker = worker
        self._set_busy(True)
        self.result = None
        self.last_ai_summary = ""
        self.scan_started.emit(str(root))
        log.info("Scan requested", extra={"ctx": {"root": str(root)}})
        self.runner.start(worker)

    def cancel(self) -> None:
        for worker in (self._scan_worker, self._exec_worker):
            if worker is not None:
                worker.cancel.set()

    def _scan_done(self, result: PipelineResult) -> None:
        self._scan_worker = None
        self.result = result
        self._set_busy(False)
        self.scan_finished.emit(result)
        self.plan_changed.emit()

    def _scan_failed(self, message: str) -> None:
        self._scan_worker = None
        self._set_busy(False)
        self.scan_failed.emit(message)

    def _scan_cancelled(self) -> None:
        self._scan_worker = None
        self._set_busy(False)
        self.scan_cancelled.emit()

    # ------------------------------------------------------------------ #
    # Execution and undo
    # ------------------------------------------------------------------ #
    def execute(self, moves: list[PlannedMove], remember_corrections: bool = True) -> None:
        if self._busy or self.result is None:
            return
        plan = self.result.plan
        corrections = [
            (m.item.name, m.classification.category)
            for m in moves
            if m.classification.source is Source.USER and not m.item.is_folder
        ]

        def job(cancel, emit):
            organiser = Organiser(self.journal, cancel, lambda d, t, n: emit((d, t, n)))
            return organiser.execute(plan, moves)

        worker = Worker(job)
        worker.signals.progress.connect(lambda a: self.execute_progress.emit(*a))
        worker.signals.finished.connect(
            lambda r: self._execute_done(r, corrections if remember_corrections else [])
        )
        worker.signals.failed.connect(self._execute_failed)
        self._exec_worker = worker
        self._set_busy(True)
        self.execute_started.emit(len(moves))
        self.runner.start(worker)

    def _execute_done(self, result: OperationResult, corrections: list[tuple[str, str]]) -> None:
        self._exec_worker = None
        if result.done and result.status is not OpStatus.CANCELLED:
            for name, category in corrections:
                self.learned.learn(name, category)
        # The plan is now stale: everything must be rescanned before more moves.
        self.result = None
        self._set_busy(False)
        self.execute_finished.emit(result)
        self.plan_changed.emit()
        self.history_changed.emit()

    def _execute_failed(self, message: str) -> None:
        self._exec_worker = None
        self.result = None
        self._set_busy(False)
        self.execute_failed.emit(message)
        self.plan_changed.emit()
        self.history_changed.emit()

    def undo(self, op_id: int, on_done: Callable[[OperationResult | str], None]) -> None:
        if self._busy:
            return

        def job(cancel, emit):
            return Organiser(self.journal, cancel).undo(op_id)

        worker = Worker(job)

        def finished(result: OperationResult) -> None:
            self._set_busy(False)
            self.result = None
            self.plan_changed.emit()
            self.history_changed.emit()
            on_done(result)

        def failed(message: str) -> None:
            self._set_busy(False)
            self.history_changed.emit()
            on_done(message)

        worker.signals.finished.connect(finished)
        worker.signals.failed.connect(failed)
        self._set_busy(True)
        self.runner.start(worker)

    def run_background(
        self,
        fn: Callable[[Any, Callable[[Any], None]], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
    ) -> Worker:
        """Run an arbitrary read-only analysis job on the pool."""
        worker = Worker(fn)
        worker.signals.finished.connect(on_done)
        if on_error:
            worker.signals.failed.connect(on_error)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        self.runner.start(worker)
        return worker

    def shutdown(self) -> None:
        self.runner.cancel_all()
        self.runner.wait(10_000)
