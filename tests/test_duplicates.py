from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from tidy.core.duplicates import DuplicateFinder, iter_files
from tidy.utils.hashing import PARTIAL_BYTES, HashCancelled


def test_no_files(downloads: Path) -> None:
    report = DuplicateFinder().find(downloads)
    assert report.groups == [] and report.wasted_bytes == 0


def test_exact_duplicates_grouped(downloads: Path, make_file) -> None:
    make_file(downloads / "a.pdf", b"same content")
    make_file(downloads / "sub" / "a (1).pdf", b"same content")
    make_file(downloads / "deep" / "x" / "copy.pdf", b"same content")
    make_file(downloads / "other.pdf", b"different!!!")  # same size, different bytes
    make_file(downloads / "unique.txt", b"u")
    report = DuplicateFinder().find(downloads)
    assert len(report.groups) == 1
    group = report.groups[0]
    assert len(group.files) == 3
    assert group.wasted == 2 * len(b"same content")
    assert report.wasted_bytes == group.wasted
    assert {f.path.name for f in group.files} == {"a.pdf", "a (1).pdf", "copy.pdf"}


def test_same_head_and_tail_different_middle(downloads: Path, make_file) -> None:
    head = b"H" * PARTIAL_BYTES
    tail = b"T" * PARTIAL_BYTES
    make_file(downloads / "one.bin", head + b"A" * 1000 + tail)
    make_file(downloads / "two.bin", head + b"B" * 1000 + tail)
    report = DuplicateFinder().find(downloads)
    assert report.groups == [], "partial-hash collisions must be resolved by full SHA-256"


def test_similar_names_but_different_content(downloads: Path, make_file) -> None:
    make_file(downloads / "report.pdf", b"version one")
    make_file(downloads / "report (1).pdf", b"version two, longer")
    make_file(downloads / "photo.jpg", b"x")
    make_file(downloads / "photo - Copy.jpg", b"x")  # true duplicate, not "similar"
    report = DuplicateFinder().find(downloads)
    assert len(report.groups) == 1
    assert [sorted(f.path.name for f in s) for s in report.similar_names] == [
        ["report (1).pdf", "report.pdf"]
    ]


def test_empty_files_ignored(downloads: Path, make_file) -> None:
    make_file(downloads / "a.txt", b"")
    make_file(downloads / "b.txt", b"")
    assert DuplicateFinder().find(downloads).groups == []


def test_hash_cache_reused_and_invalidated(downloads: Path, make_file, db) -> None:
    make_file(downloads / "a.bin", b"payload", mtime=1_600_000_000)
    make_file(downloads / "b.bin", b"payload", mtime=1_600_000_000)
    first = DuplicateFinder(db).find(downloads)
    assert first.bytes_hashed > 0
    second = DuplicateFinder(db).find(downloads)
    assert second.bytes_hashed == 0, "unchanged files must come from the cache"
    assert len(second.groups) == 1
    # Modify one file (same size): cache must not hide the change.
    (downloads / "b.bin").write_bytes(b"PAYLOAD")
    os.utime(downloads / "b.bin", (1_700_000_000, 1_700_000_000))
    third = DuplicateFinder(db).find(downloads)
    assert third.groups == []


def test_never_deletes_anything(downloads: Path, make_file) -> None:
    make_file(downloads / "a.txt", b"dup")
    make_file(downloads / "b.txt", b"dup")
    DuplicateFinder().find(downloads)
    assert sorted(p.name for p in downloads.iterdir()) == ["a.txt", "b.txt"]


def test_unreadable_file_reported(downloads: Path, make_file, monkeypatch) -> None:
    make_file(downloads / "a.txt", b"dup")
    make_file(downloads / "b.txt", b"dup")
    import tidy.core.duplicates as mod

    real = mod.partial_hash

    def flaky(path):
        if path.name == "b.txt":
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(mod, "partial_hash", flaky)
    report = DuplicateFinder().find(downloads)
    assert report.groups == []
    assert report.errors and report.errors[0][0].name == "b.txt"


def test_cancel(downloads: Path, make_file) -> None:
    for i in range(10):
        make_file(downloads / f"{i}.bin", b"same")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(HashCancelled):
        DuplicateFinder(cancel=cancel).find(downloads)


def test_iter_files_skips_links_and_partials(downloads: Path, make_file, tmp_path: Path) -> None:
    make_file(downloads / "ok.txt")
    make_file(downloads / "dl.crdownload")
    outside = tmp_path / "outside"
    make_file(outside / "secret.txt")
    import subprocess
    import sys

    if sys.platform == "win32":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(downloads / "j"), str(outside)],
            check=True,
            capture_output=True,
        )
    names = {r.path.name for r in iter_files(downloads)}
    assert names == {"ok.txt"}


@pytest.mark.slow
def test_many_files(downloads: Path) -> None:
    for i in range(2000):
        (downloads / f"f{i}.txt").write_bytes(f"content {i % 500}".encode())
    report = DuplicateFinder().find(downloads)
    assert len(report.groups) == 500
    assert all(len(g.files) == 4 for g in report.groups)
