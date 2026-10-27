from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tidy.core import scanner as scanner_mod
from tidy.core.projects import detect_project
from tidy.core.scanner import ScanCancelled, Scanner, ScanOptions
from tidy.models import ItemKind


def names(items) -> set[str]:
    return {i.name for i in items}


def test_scan_empty_directory(downloads: Path) -> None:
    s = Scanner()
    assert list(s.scan(downloads)) == []
    assert s.stats.files == 0


def test_scan_invalid_roots(tmp_path: Path, make_file) -> None:
    with pytest.raises(FileNotFoundError):
        list(Scanner().scan(tmp_path / "missing"))
    f = make_file(tmp_path / "file.txt")
    with pytest.raises(NotADirectoryError):
        list(Scanner().scan(f))


def test_scan_top_level_files_and_folders(downloads: Path, make_file) -> None:
    make_file(downloads / "a.pdf", b"pdf")
    make_file(downloads / "b.jpg", b"jpg!")
    make_file(downloads / "holiday" / "1.jpg", b"12345")
    make_file(downloads / "holiday" / "nested" / "2.jpg", b"123")
    s = Scanner()
    items = {i.name: i for i in s.scan(downloads)}
    assert set(items) == {"a.pdf", "b.jpg", "holiday"}
    folder = items["holiday"]
    assert folder.kind is ItemKind.FOLDER
    assert folder.file_count == 2
    assert folder.size == 8
    assert folder.ext_counts == {".jpg": 2}
    assert s.stats.files == 2 and s.stats.folders == 1


def test_scan_recursive_files_but_projects_kept_whole(downloads: Path, make_file) -> None:
    make_file(downloads / "misc" / "deep" / "notes.txt")
    make_file(downloads / "myapp" / "pyproject.toml")
    make_file(downloads / "myapp" / "src" / "main.py")
    items = list(Scanner(ScanOptions(include_subdirectories=True)).scan(downloads))
    by_name = {i.name: i for i in items}
    assert "notes.txt" in by_name
    assert by_name["notes.txt"].rel_path == Path("misc", "deep", "notes.txt")
    assert by_name["myapp"].kind is ItemKind.FOLDER
    assert by_name["myapp"].project_type == "python"
    assert "main.py" not in by_name


def test_skips_incomplete_downloads_and_os_metadata(downloads: Path, make_file) -> None:
    make_file(downloads / "movie.mp4.crdownload")
    make_file(downloads / "big.iso.part")
    make_file(downloads / "~$report.docx")
    make_file(downloads / "desktop.ini")
    make_file(downloads / "ok.txt")
    s = Scanner()
    assert names(s.scan(downloads)) == {"ok.txt"}
    reasons = {e.path.name: e.reason for e in s.stats.skipped}
    assert "incomplete" in reasons["movie.mp4.crdownload"]
    assert "~$report.docx" in reasons


def test_hidden_files_optional(downloads: Path, make_file) -> None:
    make_file(downloads / ".secret")
    assert names(Scanner().scan(downloads)) == set()
    assert names(Scanner(ScanOptions(include_hidden=True)).scan(downloads)) == {".secret"}


def test_skips_tidy_output_and_reserved_names(downloads: Path, make_file) -> None:
    make_file(downloads / "Documents" / "University" / "essay.pdf")
    make_file(downloads / "Sorted" / "x.txt")
    make_file(downloads / "loose.txt")
    options = ScanOptions(
        include_subdirectories=True,
        excluded_dirs=frozenset({downloads / "Sorted"}),
        reserved_top_level=frozenset({"documents"}),
    )
    assert names(Scanner(options).scan(downloads)) == {"loose.txt"}


def test_skips_protected_dirs(downloads: Path, make_file) -> None:
    make_file(downloads / "$RECYCLE.BIN" / "junk")
    make_file(downloads / "System Volume Information" / "x")
    assert names(Scanner().scan(downloads)) == set()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junctions_not_followed(tmp_path: Path, downloads: Path, make_file) -> None:
    outside = tmp_path / "outside"
    make_file(outside / "private.txt")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(downloads / "link"), str(outside)],
        check=True,
        capture_output=True,
    )
    s = Scanner(ScanOptions(include_subdirectories=True))
    assert names(s.scan(downloads)) == set()
    assert any("junction" in e.reason for e in s.stats.skipped)


def test_unusual_filenames(downloads: Path, make_file) -> None:
    weird = [
        "résumé final (copy).pdf",
        "日本語のファイル.txt",
        "emoji 🎉 party.png",
        "-leading-dash.txt",
        "multiple.dots.in.name.tar.gz",
        "no_extension",
        "UPPER.JPG",
        "spaces   inside.docx",
        "a" * 200 + ".txt",
    ]
    for n in weird:
        make_file(downloads / n)
    found = names(Scanner().scan(downloads))
    assert found == set(weird)


def test_permission_error_is_recorded_not_raised(
    downloads: Path, make_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_file(downloads / "locked" / "x.txt")
    make_file(downloads / "fine.txt")
    real_scandir = os.scandir

    def fake_scandir(path):
        if Path(path).name == "locked":
            raise PermissionError(13, "Access is denied")
        return real_scandir(path)

    monkeypatch.setattr(scanner_mod.os, "scandir", fake_scandir)
    s = Scanner(ScanOptions(include_subdirectories=True))
    assert names(s.scan(downloads)) == {"fine.txt"}
    assert any("cannot read" in e.reason for e in s.stats.skipped)


def test_cancel(downloads: Path, make_file) -> None:
    for i in range(50):
        make_file(downloads / f"{i}.txt")
    cancel = threading.Event()
    s = Scanner(cancel=cancel)
    gen = s.scan(downloads)
    next(gen)
    cancel.set()
    with pytest.raises(ScanCancelled):
        list(gen)


def test_progress_callback(downloads: Path, make_file) -> None:
    for i in range(10):
        make_file(downloads / f"{i}.txt")
    calls: list[int] = []
    list(Scanner(progress=lambda st, p: calls.append(st.files), progress_every=3).scan(downloads))
    assert calls == [3, 6, 9]


@pytest.mark.slow
def test_large_collection_is_fast(downloads: Path) -> None:
    for d in range(20):
        sub = downloads / f"dir{d}"
        sub.mkdir()
        for i in range(250):
            (sub / f"file{i}.txt").write_bytes(b"x")
    start = time.perf_counter()
    s = Scanner(ScanOptions(include_subdirectories=True))
    count = sum(1 for _ in s.scan(downloads))
    assert count == 5000
    assert time.perf_counter() - start < 10


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["pyproject.toml", "x.py"], "python"),
        (["package.json", "index.js"], "web"),
        (["Cargo.toml"], "rust"),
        (["Song.als"], "music"),
        (["beat.flp", "kick.wav"], "music"),
        (["App.sln", "README.md"], "dotnet"),
        (["a.py", "b.py", "c.py", "readme.md"], "python"),
        (["a.py", "photo1.jpg", "photo2.jpg", "photo3.jpg"], None),
        (["notes.txt"], None),
    ],
)
def test_detect_project(tmp_path: Path, make_file, files: list[str], expected: str | None) -> None:
    for f in files:
        make_file(tmp_path / "p" / f)
    assert detect_project(tmp_path / "p") == expected


def test_detect_project_git_refined_by_language(tmp_path: Path, make_file) -> None:
    (tmp_path / "p" / ".git").mkdir(parents=True)
    make_file(tmp_path / "p" / "main.go")
    assert detect_project(tmp_path / "p") == "go"
