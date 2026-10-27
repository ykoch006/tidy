"""Run engine work on Qt's thread pool and report back through signals."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from tidy.core.scanner import ScanCancelled
from tidy.utils.hashing import HashCancelled
from tidy.utils.logging import get_logger

log = get_logger(__name__)


class WorkerSignals(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class Worker(QRunnable):
    """Calls ``fn(cancel_event, emit_progress)`` on a pool thread."""

    def __init__(self, fn: Callable[[threading.Event, Callable[[Any], None]], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn(self.cancel, self.signals.progress.emit)
        except (ScanCancelled, HashCancelled):
            self.signals.cancelled.emit()
        except Exception as exc:
            log.exception("Background task failed")
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            # Operations that honour cancel part-way still return a result.
            self.signals.finished.emit(result)


class TaskRunner(QObject):
    """Keeps Python references to running workers so their signals stay alive."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._active: set[Worker] = set()

    def start(self, worker: Worker) -> Worker:
        self._active.add(worker)
        for signal in (worker.signals.finished, worker.signals.failed, worker.signals.cancelled):
            signal.connect(lambda *_a, w=worker: self._active.discard(w))
        self._pool.start(worker)
        return worker

    def cancel_all(self) -> None:
        for worker in list(self._active):
            worker.cancel.set()

    def wait(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(msecs)
