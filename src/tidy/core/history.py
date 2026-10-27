"""The persistent operation journal.

Every organisation run is recorded *before* any file is touched (write-ahead),
and each move's state is updated durably around the filesystem call. After a
crash, :meth:`Organiser.recover` uses this record to finish or roll back the
one move that may have been in flight.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tidy.database import Database
from tidy.models import ItemKind


class OpStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # finished, but some moves failed or were skipped
    CANCELLED = "cancelled"
    UNDOING = "undoing"
    UNDONE = "undone"
    PARTIALLY_UNDONE = "partially_undone"
    RECOVERED = "recovered"  # was interrupted; recovery made the journal consistent


class MoveState(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNDO_IN_PROGRESS = "undo_in_progress"
    UNDONE = "undone"
    UNDO_FAILED = "undo_failed"


@dataclass(slots=True)
class MoveRecord:
    id: int
    operation_id: int
    seq: int
    kind: ItemKind
    source: Path
    destination: Path
    size: int
    mtime_ns: int
    category: str | None
    status: MoveState
    error: str | None
    temp_path: Path | None
    undo_path: Path | None


@dataclass(slots=True)
class OperationRecord:
    id: int
    created_at: str
    finished_at: str | None
    root: Path
    status: OpStatus
    total: int
    done: int = 0
    failed: int = 0
    undone: int = 0
    bytes_moved: int = 0

    @property
    def can_undo(self) -> bool:
        return (
            self.status
            in {
                OpStatus.COMPLETED,
                OpStatus.PARTIAL,
                OpStatus.CANCELLED,
                OpStatus.RECOVERED,
                OpStatus.PARTIALLY_UNDONE,
            }
            and self.done > 0
        )


@dataclass(frozen=True, slots=True)
class NewMove:
    kind: ItemKind
    source: Path
    destination: Path
    size: int
    mtime_ns: int
    category: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _opt_path(value: str | None) -> Path | None:
    return Path(value) if value else None


class Journal:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- writes ---------------------------------------------------------- #
    def create_operation(self, root: Path, moves: list[NewMove], note: str = "") -> int:
        """Record an operation and all of its moves atomically, before execution."""
        now = _now()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO operations(created_at, root, status, total, note) VALUES(?,?,?,?,?)",
                (now, str(root), OpStatus.PENDING, len(moves), note),
            )
            op_id = int(cur.lastrowid or 0)
            conn.executemany(
                "INSERT INTO moves(operation_id, seq, kind, source, destination, size, mtime_ns, "
                "category, status, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        op_id,
                        seq,
                        m.kind,
                        str(m.source),
                        str(m.destination),
                        m.size,
                        m.mtime_ns,
                        m.category,
                        MoveState.PENDING,
                        now,
                    )
                    for seq, m in enumerate(moves)
                ],
            )
        return op_id

    def set_operation_status(self, op_id: int, status: OpStatus, finished: bool = False) -> None:
        if finished:
            self.db.execute(
                "UPDATE operations SET status=?, finished_at=? WHERE id=?", (status, _now(), op_id)
            )
        else:
            self.db.execute("UPDATE operations SET status=? WHERE id=?", (status, op_id))

    def update_move(
        self,
        move_id: int,
        status: MoveState,
        error: str | None = None,
        *,
        destination: Path | None = None,
        temp_path: Path | bool | None = False,
        undo_path: Path | None = None,
    ) -> None:
        sets = ["status=?", "error=?", "updated_at=?"]
        params: list[object] = [status, error, _now()]
        if destination is not None:
            sets.append("destination=?")
            params.append(str(destination))
        if temp_path is not False:
            sets.append("temp_path=?")
            params.append(str(temp_path) if temp_path else None)
        if undo_path is not None:
            sets.append("undo_path=?")
            params.append(str(undo_path))
        params.append(move_id)
        self.db.execute(f"UPDATE moves SET {', '.join(sets)} WHERE id=?", tuple(params))

    def record_created_dir(self, op_id: int, path: Path) -> None:
        self.db.execute(
            "INSERT INTO created_dirs(operation_id, path) VALUES(?, ?)", (op_id, str(path))
        )

    # -- reads ----------------------------------------------------------- #
    def operations(self, limit: int = 200) -> list[OperationRecord]:
        rows = self.db.query(
            """
            SELECT o.*,
                   SUM(m.status = 'done')                             AS n_done,
                   SUM(m.status IN ('failed', 'undo_failed'))         AS n_failed,
                   SUM(m.status = 'undone')                           AS n_undone,
                   SUM(CASE WHEN m.status = 'done' THEN m.size END)   AS bytes_done
            FROM operations o LEFT JOIN moves m ON m.operation_id = o.id
            GROUP BY o.id ORDER BY o.id DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            OperationRecord(
                r["id"],
                r["created_at"],
                r["finished_at"],
                Path(r["root"]),
                OpStatus(r["status"]),
                r["total"],
                r["n_done"] or 0,
                r["n_failed"] or 0,
                r["n_undone"] or 0,
                r["bytes_done"] or 0,
            )
            for r in rows
        ]

    def operation(self, op_id: int) -> OperationRecord | None:
        return next((o for o in self.operations(10_000) if o.id == op_id), None)

    def moves(self, op_id: int) -> list[MoveRecord]:
        rows = self.db.query("SELECT * FROM moves WHERE operation_id=? ORDER BY seq", (op_id,))
        return [self._move(r) for r in rows]

    def moves_in_state(self, *states: MoveState) -> list[MoveRecord]:
        marks = ",".join("?" * len(states))
        rows = self.db.query(
            f"SELECT * FROM moves WHERE status IN ({marks}) ORDER BY id", tuple(states)
        )
        return [self._move(r) for r in rows]

    def operations_in_state(self, *states: OpStatus) -> list[int]:
        marks = ",".join("?" * len(states))
        rows = self.db.query(f"SELECT id FROM operations WHERE status IN ({marks})", tuple(states))
        return [r["id"] for r in rows]

    def created_dirs(self, op_id: int) -> list[Path]:
        rows = self.db.query(
            "SELECT path FROM created_dirs WHERE operation_id=? ORDER BY id", (op_id,)
        )
        return [Path(r["path"]) for r in rows]

    def all_created_dirs(self) -> set[Path]:
        return {Path(r["path"]) for r in self.db.query("SELECT DISTINCT path FROM created_dirs")}

    @staticmethod
    def _move(r) -> MoveRecord:  # type: ignore[no-untyped-def]
        return MoveRecord(
            r["id"],
            r["operation_id"],
            r["seq"],
            ItemKind(r["kind"]),
            Path(r["source"]),
            Path(r["destination"]),
            r["size"],
            r["mtime_ns"],
            r["category"],
            MoveState(r["status"]),
            r["error"],
            _opt_path(r["temp_path"]),
            _opt_path(r["undo_path"]),
        )
