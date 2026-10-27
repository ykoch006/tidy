"""Exact duplicate detection.

Files are compared in three increasingly expensive passes — size, a partial
hash of the first/last 64 KB, and finally a full SHA-256 — so only genuine
candidates are ever read completely. Hashes are cached in SQLite keyed by
path, size and modification time.

Tidy never deletes duplicates; it only reports them.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tidy.core.grouping import base_stem
from tidy.core.scanner import INCOMPLETE_SUFFIXES
from tidy.database import Database
from tidy.utils import paths
from tidy.utils.hashing import PARTIAL_BYTES, HashCancelled, partial_hash, sha256_file
from tidy.utils.logging import get_logger

log = get_logger(__name__)

# (phase, done, total)
DuplicateProgress = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class FileRef:
    path: Path
    size: int
    mtime_ns: int


@dataclass(slots=True)
class DuplicateGroup:
    sha256: str
    size: int
    files: list[FileRef]

    @property
    def wasted(self) -> int:
        return self.size * (len(self.files) - 1)


@dataclass(slots=True)
class DuplicateReport:
    root: Path
    groups: list[DuplicateGroup] = field(default_factory=list)
    similar_names: list[list[FileRef]] = field(default_factory=list)
    files_checked: int = 0
    bytes_hashed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted for g in self.groups)

    def group_for(self) -> dict[Path, DuplicateGroup]:
        return {f.path: g for g in self.groups for f in g.files}


def iter_files(
    root: Path, include_hidden: bool = False, cancel: threading.Event | None = None
) -> Iterator[FileRef]:
    """Every regular file below ``root``, never following links or junctions."""
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            raise HashCancelled()
        directory = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if paths.is_link_like(path, st):
                continue
            if not include_hidden and paths.is_hidden_or_system(path, st):
                continue
            if entry.is_dir(follow_symlinks=False):
                if not paths.is_protected_dir_name(entry.name):
                    stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                if paths.is_ignored_file_name(entry.name):
                    continue
                if path.suffix.lower() in INCOMPLETE_SUFFIXES:
                    continue
                yield FileRef(path, st.st_size, st.st_mtime_ns)


class HashCache:
    def __init__(self, db: Database | None) -> None:
        self.db = db

    def get(self, ref: FileRef, kind: str) -> str | None:
        if self.db is None:
            return None
        row = self.db.query_one(
            f"SELECT {kind} FROM hash_cache WHERE path=? AND size=? AND mtime_ns=?",
            (str(ref.path), ref.size, ref.mtime_ns),
        )
        return row[kind] if row and row[kind] else None

    def put(self, ref: FileRef, kind: str, value: str) -> None:
        if self.db is None:
            return
        self.db.execute(
            "INSERT INTO hash_cache(path, size, mtime_ns, partial, full) VALUES(?,?,?,NULL,NULL) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns, "
            "partial=CASE WHEN hash_cache.size=excluded.size AND "
            "hash_cache.mtime_ns=excluded.mtime_ns THEN hash_cache.partial END, "
            "full=CASE WHEN hash_cache.size=excluded.size AND "
            "hash_cache.mtime_ns=excluded.mtime_ns THEN hash_cache.full END",
            (str(ref.path), ref.size, ref.mtime_ns),
        )
        self.db.execute(f"UPDATE hash_cache SET {kind}=? WHERE path=?", (value, str(ref.path)))


class DuplicateFinder:
    def __init__(
        self,
        db: Database | None = None,
        cancel: threading.Event | None = None,
        progress: DuplicateProgress | None = None,
        min_size: int = 1,
    ) -> None:
        self.cache = HashCache(db)
        self.cancel = cancel or threading.Event()
        self.progress = progress
        self.min_size = min_size

    def _check(self) -> None:
        if self.cancel.is_set():
            raise HashCancelled()

    def _hash(self, ref: FileRef, kind: str, report: DuplicateReport) -> str | None:
        cached = self.cache.get(ref, kind)
        if cached:
            return cached
        try:
            if kind == "partial":
                value = partial_hash(ref.path)
                report.bytes_hashed += min(ref.size, 2 * PARTIAL_BYTES)
            else:
                value = sha256_file(ref.path, self.cancel)
                report.bytes_hashed += ref.size
        except OSError as exc:
            report.errors.append((ref.path, exc.strerror or str(exc)))
            return None
        self.cache.put(ref, kind, value)
        return value

    def find(self, root: Path, files: Iterable[FileRef] | None = None) -> DuplicateReport:
        report = DuplicateReport(root=root)
        refs = list(files) if files is not None else list(iter_files(root, cancel=self.cancel))
        report.files_checked = len(refs)

        by_size: dict[int, list[FileRef]] = defaultdict(list)
        for ref in refs:
            if ref.size >= self.min_size:
                by_size[ref.size].append(ref)
        candidates = [group for group in by_size.values() if len(group) > 1]

        # Pass 2: partial hash
        total = sum(len(g) for g in candidates)
        done = 0
        partial_groups: list[list[FileRef]] = []
        for group in candidates:
            buckets: dict[str, list[FileRef]] = defaultdict(list)
            for ref in group:
                self._check()
                value = self._hash(ref, "partial", report)
                done += 1
                if self.progress and done % 25 == 0:
                    self.progress("partial", done, total)
                if value:
                    buckets[value].append(ref)
            partial_groups.extend(b for b in buckets.values() if len(b) > 1)

        # Pass 3: full SHA-256 (small files are fully covered by the partial hash
        # already, but we still compute the real digest so groups are keyed by it).
        total = sum(len(g) for g in partial_groups)
        done = 0
        for group in partial_groups:
            buckets = defaultdict(list)
            for ref in group:
                self._check()
                value = self._hash(ref, "full", report)
                done += 1
                if self.progress:
                    self.progress("full", done, total)
                if value:
                    buckets[value].append(ref)
            for digest, members in buckets.items():
                if len(members) > 1:
                    members.sort(key=lambda r: (r.mtime_ns, str(r.path).casefold()))
                    report.groups.append(DuplicateGroup(digest, members[0].size, members))
        report.groups.sort(key=lambda g: g.wasted, reverse=True)

        report.similar_names = self._similar_names(refs, report)
        log.info(
            "Duplicate search finished",
            extra={
                "ctx": {
                    "files": report.files_checked,
                    "groups": len(report.groups),
                    "wasted": report.wasted_bytes,
                    "hashed": report.bytes_hashed,
                }
            },
        )
        return report

    @staticmethod
    def _similar_names(refs: list[FileRef], report: DuplicateReport) -> list[list[FileRef]]:
        """Files that *look* like copies by name (``x (1).pdf``) but differ in content."""
        in_group = {f.path for g in report.groups for f in g.files}
        by_name: dict[tuple[str, str], list[FileRef]] = defaultdict(list)
        for ref in refs:
            by_name[(base_stem(ref.path.name), ref.path.suffix.lower())].append(ref)
        similar: list[list[FileRef]] = []
        for members in by_name.values():
            if len(members) < 2:
                continue
            distinct = [m for m in members if m.path not in in_group]
            names = {m.path.name.casefold() for m in members}
            # Only interesting when names actually vary or sizes differ.
            if len(distinct) >= 2 and (len(names) > 1 or len({m.size for m in distinct}) > 1):
                similar.append(sorted(distinct, key=lambda r: str(r.path).casefold()))
        return similar
