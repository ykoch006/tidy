"""Plain data models shared by the core engine and the UI.

These are deliberately free of Qt and SQLite so the engine can be tested and
reused headlessly.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path, PurePath


class ItemKind(enum.StrEnum):
    FILE = "file"
    FOLDER = "folder"  # a directory moved as one unit (e.g. a project)


@dataclass(slots=True)
class FileItem:
    """A file, or a folder treated as a single unit, discovered by the scanner."""

    path: Path
    root: Path
    size: int
    mtime_ns: int
    kind: ItemKind = ItemKind.FILE
    project_type: str | None = None  # set when a folder is a recognised project
    file_count: int = 1  # number of files inside, for folders
    sample_names: tuple[str, ...] = ()  # for folders: some contained file names
    ext_counts: dict[str, int] = field(default_factory=dict)  # for folders

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.lower() if self.kind is ItemKind.FILE else ""

    @property
    def rel_path(self) -> Path:
        return self.path.relative_to(self.root)

    @property
    def is_folder(self) -> bool:
        return self.kind is ItemKind.FOLDER


class Source(enum.StrEnum):
    RULE = "rule"
    LEARNED = "learned"
    CONTENT = "content"
    GROUP = "group"
    AI = "ai"
    USER = "user"
    FALLBACK = "fallback"


@dataclass(slots=True)
class Classification:
    category: str  # category key, e.g. "documents.university"
    confidence: float
    source: Source
    reasons: list[str] = field(default_factory=list)

    def explain(self) -> str:
        return "; ".join(self.reasons) if self.reasons else self.source.value


class MoveStatus(enum.StrEnum):
    PROPOSED = "proposed"
    UNCHANGED = "unchanged"  # already in the right place
    SKIPPED = "skipped"


@dataclass(slots=True)
class PlannedMove:
    item: FileItem
    classification: Classification
    destination: Path
    approved: bool = True
    status: MoveStatus = MoveStatus.PROPOSED
    group_id: str | None = None
    note: str = ""
    custom_folder: PurePath | None = None  # user-edited relative destination folder
    edited: bool = False  # user changed the category or folder

    @property
    def is_noop(self) -> bool:
        return self.status is not MoveStatus.PROPOSED


@dataclass(slots=True)
class Plan:
    root: Path
    moves: list[PlannedMove] = field(default_factory=list)

    def actionable(self) -> list[PlannedMove]:
        return [m for m in self.moves if m.approved and not m.is_noop]

    @property
    def total_size(self) -> int:
        return sum(m.item.size for m in self.moves)
