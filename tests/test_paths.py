from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath

import pytest

from tidy.utils import paths
from tidy.utils.formatting import human_size, percent
from tidy.utils.paths import UnsafePathError


def test_downloads_override_points_at_sandbox(downloads: Path) -> None:
    assert paths.default_downloads_dir() == downloads


def test_data_dir_is_isolated(tmp_path: Path) -> None:
    assert paths.data_dir() == tmp_path / "tidy-data"
    assert paths.data_dir().is_dir()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("a<b>c.txt", "a_b_c.txt"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("trailing. ", "trailing"),
        ("..", "_"),
        ("", "_"),
        ("日本語 ファイル.txt", "日本語 ファイル.txt"),
    ],
)
def test_sanitize_component(raw: str, expected: str) -> None:
    assert paths.sanitize_component(raw) == expected


def test_sanitize_truncates_long_names() -> None:
    name = "a" * 400 + ".pdf"
    cleaned = paths.sanitize_component(name)
    assert len(cleaned) <= paths.MAX_COMPONENT_LENGTH
    assert cleaned.endswith(".pdf")


@pytest.mark.parametrize(
    "bad",
    ["../escape", "C:\\Windows", "/etc", "\\\\server\\share", "a/../b", "ok/CON", "x/y:z", "a/b. "],
)
def test_parse_relative_folder_rejects_unsafe(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        paths.parse_relative_folder(bad)


def test_parse_relative_folder_accepts_both_separators() -> None:
    assert paths.parse_relative_folder("Documents\\University/2026") == PurePath(
        "Documents", "University", "2026"
    )
    assert paths.parse_relative_folder("  ") == PurePath()


def test_ensure_within(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert paths.ensure_within(root, root / "a" / "b.txt") == (root / "a" / "b.txt").resolve()
    with pytest.raises(UnsafePathError):
        paths.ensure_within(root, root / ".." / "outside.txt")


def test_unique_destination_never_reuses_existing(tmp_path: Path, make_file) -> None:
    make_file(tmp_path / "file.txt")
    make_file(tmp_path / "file (1).txt")
    reserved = [tmp_path / "FILE (2).txt"]  # case-insensitive reservation
    assert paths.unique_destination(tmp_path / "file.txt", reserved) == tmp_path / "file (3).txt"
    assert paths.unique_destination(tmp_path / "new.txt") == tmp_path / "new.txt"


def test_unique_destination_for_folders(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    assert paths.unique_destination(tmp_path / "project") == tmp_path / "project (1)"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junction_detected_as_link(tmp_path: Path) -> None:
    import subprocess

    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True
    )
    assert paths.is_link_like(junction)
    assert not paths.is_link_like(target)


def test_symlink_detected_as_link(tmp_path: Path) -> None:
    target = tmp_path / "t.txt"
    target.write_text("x")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted for this user")
    assert paths.is_link_like(link)


def test_assert_no_links_between(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    paths.assert_no_links_between(root, root / "real" / "x.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("symlinks not permitted")
        import subprocess

        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True,
                       capture_output=True)
    with pytest.raises(UnsafePathError):
        paths.assert_no_links_between(root, link / "x.txt")


def test_same_volume_for_nonexistent_paths(tmp_path: Path) -> None:
    assert paths.same_volume(tmp_path / "a" / "b", tmp_path / "c")


def test_formatting() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024**3) == "5.0 GB"
    assert percent(0.876) == "88%"
