"""Execute approved plans, undo them, and recover from interruptions.

Safety model
------------
1. The whole operation is journaled before anything moves (write-ahead).
2. Each source is re-checked immediately before its move; anything that
   changed since the scan, vanished, or became a link is skipped.
3. Destinations are re-validated (inside the root, no links on the way, never
   an existing name) at execution time, not just at planning time.
4. A move is marked ``in_progress`` durably before the filesystem call and
   ``done`` after it, so at most one move per operation is ever ambiguous —
   and :meth:`Organiser.recover` resolves that one deterministically.
5. Undo uses the same primitives in reverse and never overwrites: if the
   original location is occupied, the item is restored beside it.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tidy.core.fileops import (
    TransferError,
    discard_temp,
    move_item,
    remove_original,
    verify_tree,
)
from tidy.core.history import Journal, MoveRecord, MoveState, NewMove, OpStatus
from tidy.core.scanner import summarise_folder
from tidy.models import ItemKind, Plan, PlannedMove
from tidy.utils import paths
from tidy.utils.hashing import sha256_file
from tidy.utils.logging import get_logger
from tidy.utils.paths import UnsafePathError

log = get_logger(__name__)

# (done, total, current item name)
OrganiserProgress = Callable[[int, int, str], None]


@dataclass
class OperationResult:
    operation_id: int
    status: OpStatus
    done: int = 0
    failed: int = 0
    skipped: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass
class RecoveryReport:
    resolved: list[str] = field(default_factory=list)
    operations: list[int] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.resolved or self.operations)


class SourceChanged(Exception):
    pass


def fingerprint(path: Path, kind: ItemKind) -> tuple[int, int]:
    """(size, newest mtime) — the same values the scanner recorded."""
    if kind is ItemKind.FOLDER:
        summary = summarise_folder(path)
        return summary.size, summary.mtime_ns
    st = path.stat()
    return st.st_size, st.st_mtime_ns


class Organiser:
    def __init__(
        self,
        journal: Journal,
        cancel: threading.Event | None = None,
        progress: OrganiserProgress | None = None,
    ) -> None:
        self.journal = journal
        self.cancel = cancel or threading.Event()
        self.progress = progress

    # ================================================================== #
    # Execute
    # ================================================================== #
    def execute(self, plan: Plan, moves: list[PlannedMove] | None = None) -> OperationResult:
        """Carry out ``moves`` (default: every approved, actionable move in ``plan``)."""
        moves = plan.actionable() if moves is None else [m for m in moves if not m.is_noop]
        root = plan.root
        records = [
            NewMove(
                m.item.kind,
                m.item.path,
                m.destination,
                m.item.size,
                m.item.mtime_ns,
                m.classification.category,
            )
            for m in moves
        ]
        op_id = self.journal.create_operation(root, records)
        self.journal.set_operation_status(op_id, OpStatus.RUNNING)
        log.info(
            "Operation started",
            extra={"ctx": {"op": op_id, "moves": len(moves), "root": str(root)}},
        )
        result = OperationResult(op_id, OpStatus.RUNNING)
        pending = self.journal.moves(op_id)

        for index, record in enumerate(pending):
            if not self.cancel.is_set():
                self._report(index, len(pending), record.source.name)
            if self.cancel.is_set():
                self.journal.update_move(record.id, MoveState.SKIPPED, "cancelled before start")
                result.skipped += 1
                continue
            try:
                final = self._execute_one(op_id, root, record)
            except SourceChanged as exc:
                self.journal.update_move(record.id, MoveState.SKIPPED, str(exc))
                result.skipped += 1
                result.messages.append(f"Skipped {record.source.name}: {exc}")
            except (OSError, UnsafePathError) as exc:
                message = _describe_error(exc)
                self.journal.update_move(record.id, MoveState.FAILED, message)
                result.failed += 1
                result.messages.append(f"Failed {record.source.name}: {message}")
                log.warning(
                    "Move failed",
                    extra={"ctx": {"op": op_id, "src": str(record.source), "error": message}},
                )
            else:
                result.done += 1
                if final != record.destination:
                    result.messages.append(
                        f"{record.source.name} was saved as '{final.name}' to avoid overwriting"
                    )
        self._report(len(pending), len(pending), "")

        if self.cancel.is_set():
            result.status = OpStatus.CANCELLED
        elif result.failed or result.skipped:
            result.status = OpStatus.PARTIAL
        else:
            result.status = OpStatus.COMPLETED
        self.journal.set_operation_status(op_id, result.status, finished=True)
        log.info(
            "Operation finished",
            extra={
                "ctx": {
                    "op": op_id,
                    "status": result.status,
                    "done": result.done,
                    "failed": result.failed,
                    "skipped": result.skipped,
                }
            },
        )
        return result

    def _execute_one(self, op_id: int, root: Path, record: MoveRecord) -> Path:
        src = record.source
        # --- validate source --------------------------------------------- #
        if not os.path.lexists(src):
            raise SourceChanged("no longer exists")
        if paths.is_link_like(src):
            raise SourceChanged("is now a link or junction")
        try:
            size, mtime = fingerprint(src, record.kind)
        except OSError as exc:
            raise SourceChanged(f"cannot be read ({_describe_error(exc)})") from exc
        if (size, mtime) != (record.size, record.mtime_ns):
            raise SourceChanged("changed since it was scanned — rescan to include it")

        # --- validate destination ---------------------------------------- #
        paths.ensure_within(root, record.destination)
        paths.assert_no_links_between(root, record.destination)
        destination = paths.unique_destination(record.destination)
        self._make_parents(op_id, root, destination.parent)

        # --- move ----------------------------------------------------------- #
        self.journal.update_move(record.id, MoveState.IN_PROGRESS, destination=destination)
        move_item(
            src,
            destination,
            record.kind,
            on_temp=lambda t: self.journal.update_move(
                record.id, MoveState.IN_PROGRESS, temp_path=t
            ),
        )
        self.journal.update_move(record.id, MoveState.DONE, temp_path=None)
        return destination

    def _make_parents(self, op_id: int, root: Path, folder: Path) -> None:
        missing: list[Path] = []
        current = folder
        while not current.exists():
            missing.append(current)
            if current.parent == current or current == root:
                break
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
            self.journal.record_created_dir(op_id, directory)

    # ================================================================== #
    # Undo
    # ================================================================== #
    def undo(self, op_id: int) -> OperationResult:
        """Move every completed item of an operation back where it came from."""
        op = self.journal.operation(op_id)
        if op is None:
            raise ValueError(f"No operation {op_id}")
        if op.status in {OpStatus.RUNNING, OpStatus.UNDOING}:
            raise RuntimeError("That operation is still running")
        self.journal.set_operation_status(op_id, OpStatus.UNDOING)
        result = OperationResult(op_id, OpStatus.UNDOING)
        done = [m for m in self.journal.moves(op_id) if m.status is MoveState.DONE]
        done.reverse()  # last in, first out

        for index, record in enumerate(done):
            if self.cancel.is_set():
                break
            self._report(index, len(done), record.destination.name)
            try:
                restored = self._undo_one(record)
            except (OSError, UnsafePathError, SourceChanged) as exc:
                message = _describe_error(exc) if isinstance(exc, OSError) else str(exc)
                self.journal.update_move(record.id, MoveState.UNDO_FAILED, message)
                result.failed += 1
                result.messages.append(f"Could not restore {record.destination.name}: {message}")
            else:
                result.done += 1
                if restored != record.source:
                    result.messages.append(
                        f"{record.source.name} restored as '{restored.name}' because the "
                        "original location is now occupied"
                    )
        self._report(len(done), len(done), "")
        self._remove_empty_created_dirs(op_id)

        remaining = [m for m in self.journal.moves(op_id) if m.status is MoveState.DONE]
        result.status = OpStatus.PARTIALLY_UNDONE if remaining or result.failed else OpStatus.UNDONE
        self.journal.set_operation_status(op_id, result.status, finished=True)
        log.info(
            "Undo finished",
            extra={
                "ctx": {
                    "op": op_id,
                    "status": result.status,
                    "restored": result.done,
                    "failed": result.failed,
                }
            },
        )
        return result

    def _undo_one(self, record: MoveRecord) -> Path:
        current = record.destination
        if not os.path.lexists(current):
            raise SourceChanged("it is no longer where Tidy put it")
        if paths.is_link_like(current):
            raise SourceChanged("its location is now a link or junction")
        record.source.parent.mkdir(parents=True, exist_ok=True)
        if paths.is_link_like(record.source.parent):
            raise SourceChanged("the original folder is now a link or junction")
        target = paths.unique_destination(record.source)
        self.journal.update_move(record.id, MoveState.UNDO_IN_PROGRESS, undo_path=target)
        move_item(
            current,
            target,
            record.kind,
            on_temp=lambda t: self.journal.update_move(
                record.id, MoveState.UNDO_IN_PROGRESS, temp_path=t
            ),
        )
        self.journal.update_move(record.id, MoveState.UNDONE, temp_path=None)
        return target

    def _remove_empty_created_dirs(self, op_id: int) -> None:
        """Remove folders this operation created, deepest first, only if empty."""
        for directory in sorted(
            self.journal.created_dirs(op_id), key=lambda p: len(p.parts), reverse=True
        ):
            try:
                if directory.is_dir() and not paths.is_link_like(directory):
                    directory.rmdir()  # fails (harmlessly) if anything is inside
            except OSError:
                pass

    # ================================================================== #
    # Recovery
    # ================================================================== #
    def recover(self) -> RecoveryReport:
        """Make the journal and filesystem consistent after a crash.

        Safe to call on every start-up; it does nothing when there is nothing
        to recover.
        """
        report = RecoveryReport()
        for record in self.journal.moves_in_state(MoveState.IN_PROGRESS):
            report.resolved.append(self._recover_move(record, undo=False))
        for record in self.journal.moves_in_state(MoveState.UNDO_IN_PROGRESS):
            report.resolved.append(self._recover_move(record, undo=True))

        for op_id in self.journal.operations_in_state(
            OpStatus.RUNNING, OpStatus.PENDING, OpStatus.UNDOING
        ):
            for record in self.journal.moves(op_id):
                if record.status is MoveState.PENDING:
                    self.journal.update_move(
                        record.id, MoveState.SKIPPED, "not started — Tidy was interrupted"
                    )
            self.journal.set_operation_status(op_id, OpStatus.RECOVERED, finished=True)
            report.operations.append(op_id)
        if report.needed:
            log.warning(
                "Recovered interrupted operations",
                extra={"ctx": {"moves": report.resolved, "ops": report.operations}},
            )
        return report

    def _recover_move(self, record: MoveRecord, undo: bool) -> str:
        if undo:
            origin, target = record.destination, record.undo_path or record.source
            ok, back = MoveState.UNDONE, MoveState.DONE
        else:
            origin, target = record.source, record.destination
            ok, back = MoveState.DONE, MoveState.FAILED
        temp = record.temp_path
        origin_exists = os.path.lexists(origin)
        target_exists = os.path.lexists(target)
        temp_exists = temp is not None and os.path.lexists(temp)
        name = origin.name

        if target_exists and not origin_exists:
            state, note = ok, "move had completed"
        elif origin_exists and not target_exists:
            if temp_exists:
                _discard(temp)  # partial cross-volume copy made by Tidy
            state, note = back, "interrupted before completing; original kept in place"
        elif origin_exists and target_exists:
            # Copy finished and was renamed into place, but the original was
            # not yet removed — or a hard link was created (POSIX rename).
            if _same_content(origin, target, record.kind):
                try:
                    remove_original(origin, target, record.kind)
                    state, note = ok, "finished an interrupted move after verifying the copy"
                except TransferError as exc:
                    state, note = (ok if record.kind is ItemKind.FOLDER else back), str(exc)
            else:
                state, note = back, "both locations hold different data; left both untouched"
        elif temp_exists and temp is not None:
            # Only the verified temp copy survives: complete the rename.
            os.rename(temp, target)
            state, note = ok, "completed an interrupted move from its verified copy"
        else:
            state, note = back, "item could not be found at either location"
        self.journal.update_move(record.id, state, None if state is ok else note, temp_path=None)
        return f"{name}: {note}"

    # ------------------------------------------------------------------ #
    def _report(self, done: int, total: int, name: str) -> None:
        if self.progress:
            self.progress(done, total, name)


def _discard(path: Path) -> None:
    discard_temp(path)


def _same_content(a: Path, b: Path, kind: ItemKind) -> bool:
    try:
        if os.path.samefile(a, b):
            return True  # hard link (POSIX no-overwrite rename)
        if kind is ItemKind.FOLDER:
            verify_tree(a, b)
            return True
        return a.stat().st_size == b.stat().st_size and sha256_file(a) == sha256_file(b)
    except (OSError, TransferError):
        return False


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "access denied or the file is open in another program"
    if isinstance(exc, FileExistsError):
        return "a file with that name already exists"
    if isinstance(exc, FileNotFoundError):
        return "file or folder not found"
    if isinstance(exc, OSError) and exc.errno == 28:
        return "not enough disk space"
    return str(exc) or exc.__class__.__name__
