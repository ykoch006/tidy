"""Filesystem scanner.

The scanner is a generator so callers can stream results into the UI and
cancel at any point. It never follows symlinks or junctions, skips protected
system folders, in-progress downloads and Tidy's own output folders, and
records everything it skipped so the user can see why.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tidy.core.projects import detect_project
from tidy.models import FileItem, ItemKind
from tidy.utils import paths
from tidy.utils.logging import get_logger

log = get_logger(__name__)

# Extensions of downloads that are still being written by a browser or tool.
INCOMPLETE_SUFFIXES = frozenset(
    {
        ".crdownload",
        ".part",
        ".partial",
        ".download",
        ".opdownload",
        ".!ut",
        ".tmp",
        ".tidy-partial",
    }
)
MAX_SAMPLE_NAMES = 40


class ScanCancelled(Exception):
    """Raised inside the generator when the cancel event is set."""


@dataclass(frozen=True, slots=True)
class ScanOptions:
    include_subdirectories: bool = False
    include_hidden: bool = False
    # Absolute directories that are never entered (e.g. folders Tidy created).
    excluded_dirs: frozenset[Path] = frozenset()
    # Top-level folder names that are Tidy's category roots ("Documents", ...).
    reserved_top_level: frozenset[str] = frozenset()
    max_depth: int = 32


@dataclass(slots=True)
class SkippedEntry:
    path: Path
    reason: str


@dataclass(slots=True)
class ScanStats:
    files: int = 0
    folders: int = 0
    bytes: int = 0
    skipped: list[SkippedEntry] = field(default_factory=list)


ProgressCallback = Callable[[ScanStats, Path], None]


class Scanner:
    def __init__(
        self,
        options: ScanOptions | None = None,
        cancel: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        progress_every: int = 200,
    ) -> None:
        self.options = options or ScanOptions()
        self.cancel = cancel or threading.Event()
        self.progress = progress
        self.progress_every = max(1, progress_every)
        self.stats = ScanStats()
        self._excluded = {self._key(p) for p in self.options.excluded_dirs}
        self._reserved = {n.casefold() for n in self.options.reserved_top_level}

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(path))

    # ------------------------------------------------------------------ #
    def scan(self, root: Path) -> Iterator[FileItem]:
        """Yield the items under ``root``. Raises ``NotADirectoryError`` for bad roots."""
        root = Path(os.path.abspath(root))
        if not root.exists():
            raise FileNotFoundError(f"Folder does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Not a folder: {root}")
        if paths.is_link_like(root):
            raise PermissionError(f"Refusing to scan a symlink or junction: {root}")
        self.stats = ScanStats()
        log.info("Scan started", extra={"ctx": {"options": repr(self.options)}})
        yield from self._walk(root, root, depth=0)
        log.info(
            "Scan finished",
            extra={
                "ctx": {
                    "files": self.stats.files,
                    "folders": self.stats.folders,
                    "skipped": len(self.stats.skipped),
                }
            },
        )

    def _skip(self, path: Path, reason: str) -> None:
        self.stats.skipped.append(SkippedEntry(path, reason))

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise ScanCancelled()

    def _tick(self, path: Path) -> None:
        count = self.stats.files + self.stats.folders
        if self.progress and count % self.progress_every == 0:
            self.progress(self.stats, path)

    def _walk(self, root: Path, directory: Path, depth: int) -> Iterator[FileItem]:
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name.casefold())
        except OSError as exc:
            self._skip(directory, f"cannot read folder: {exc.strerror or exc}")
            return

        for entry in entries:
            self._check_cancel()
            path = Path(entry.path)
            name = entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                self._skip(path, f"cannot read: {exc.strerror or exc}")
                continue

            if paths.is_link_like(path, st):
                self._skip(path, "symlink or junction (not followed)")
                continue
            if paths.is_ignored_file_name(name):
                continue
            if not self.options.include_hidden and paths.is_hidden_or_system(path, st):
                self._skip(path, "hidden or system item")
                continue

            if entry.is_dir(follow_symlinks=False):
                yield from self._handle_dir(root, path, depth)
            elif entry.is_file(follow_symlinks=False):
                if path.suffix.lower() in INCOMPLETE_SUFFIXES or name.startswith("~$"):
                    self._skip(path, "incomplete download or temporary file")
                    continue
                self.stats.files += 1
                self.stats.bytes += st.st_size
                self._tick(path)
                yield FileItem(path=path, root=root, size=st.st_size, mtime_ns=st.st_mtime_ns)
            else:
                self._skip(path, "not a regular file")

    def _handle_dir(self, root: Path, path: Path, depth: int) -> Iterator[FileItem]:
        if paths.is_protected_dir_name(path.name):
            self._skip(path, "protected system folder")
            return
        if self._key(path) in self._excluded:
            self._skip(path, "folder created by Tidy")
            return
        if depth == 0 and path.name.casefold() in self._reserved:
            self._skip(path, "organised category folder")
            return

        project_type = detect_project(path)
        if project_type is not None or not self.options.include_subdirectories:
            item = self._folder_item(root, path, project_type)
            if item is not None:
                self.stats.folders += 1
                self.stats.bytes += item.size
                self._tick(path)
                yield item
            return
        if depth + 1 > self.options.max_depth:
            self._skip(path, "too deeply nested")
            return
        yield from self._walk(root, path, depth + 1)

    def _folder_item(self, root: Path, path: Path, project_type: str | None) -> FileItem | None:
        """Summarise a folder that will be moved as one unit."""
        try:
            summary = summarise_folder(path, self.cancel)
        except OSError as exc:
            self._skip(path, f"cannot read folder: {exc.strerror or exc}")
            return None
        for unreadable in summary.unreadable:
            self._skip(unreadable, "cannot read folder")
        return FileItem(
            path=path,
            root=root,
            size=summary.size,
            mtime_ns=summary.mtime_ns,
            kind=ItemKind.FOLDER,
            project_type=project_type,
            file_count=summary.file_count,
            sample_names=tuple(summary.samples),
            ext_counts=dict(summary.ext_counts),
        )


@dataclass(slots=True)
class FolderSummary:
    size: int = 0
    file_count: int = 0
    mtime_ns: int = 0  # newest of the folder itself and anything inside
    link_count: int = 0  # links/junctions inside (never followed)
    ext_counts: Counter[str] = field(default_factory=Counter)
    samples: list[str] = field(default_factory=list)
    unreadable: list[Path] = field(default_factory=list)


def summarise_folder(path: Path, cancel: threading.Event | None = None) -> FolderSummary:
    """Walk a folder (without following links) and summarise it.

    Also used by the organiser to fingerprint a folder just before moving it.
    Raises ``OSError`` only if ``path`` itself cannot be read.
    """
    summary = FolderSummary(mtime_ns=path.stat().st_mtime_ns)
    stack = [path]
    while stack:
        if cancel is not None and cancel.is_set():
            raise ScanCancelled()
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    child = Path(entry.path)
                    if paths.is_link_like(child, st):
                        summary.link_count += 1
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        summary.mtime_ns = max(summary.mtime_ns, st.st_mtime_ns)
                        stack.append(child)
                    else:
                        summary.file_count += 1
                        summary.size += st.st_size
                        summary.mtime_ns = max(summary.mtime_ns, st.st_mtime_ns)
                        summary.ext_counts[os.path.splitext(entry.name)[1].lower()] += 1
                        if len(summary.samples) < MAX_SAMPLE_NAMES:
                            summary.samples.append(entry.name)
        except OSError:
            if current == path:
                raise
            summary.unreadable.append(current)
    return summary
