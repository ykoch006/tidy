"""Shared fixtures.

Every test runs with ``TIDY_DATA_DIR`` and ``TIDY_DOWNLOADS_DIR`` pointing into
a fresh temporary directory, so no test can read or modify the real Downloads
folder or the user's Tidy database.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Resolve the real Downloads folder *before* any override, purely so the guard
# below can prove tests never point at it.
from tidy.utils import paths as _paths

_saved = os.environ.pop("TIDY_DOWNLOADS_DIR", None)
REAL_DOWNLOADS = _paths.default_downloads_dir().resolve()
if _saved is not None:
    os.environ["TIDY_DOWNLOADS_DIR"] = _saved


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    assert not tmp_path.resolve().is_relative_to(REAL_DOWNLOADS), "tmp dir inside real Downloads"
    sandbox = tmp_path / "sandbox-downloads"
    sandbox.mkdir()
    monkeypatch.setenv("TIDY_DATA_DIR", str(tmp_path / "tidy-data"))
    monkeypatch.setenv("TIDY_DOWNLOADS_DIR", str(sandbox))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    """The synthetic Downloads folder for this test."""
    return tmp_path / "sandbox-downloads"


@pytest.fixture
def db(tmp_path: Path):
    from tidy.database import Database

    database = Database(tmp_path / "tidy-data" / "test.sqlite3")
    yield database
    database.close()


@pytest.fixture
def make_file() -> Callable[..., Path]:
    """Create a file with optional content and modification time."""

    def _make(path: Path, content: bytes | str = b"x", mtime: float | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        path.write_bytes(data)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    return _make
