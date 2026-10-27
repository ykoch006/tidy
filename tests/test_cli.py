from __future__ import annotations

import json
from pathlib import Path

import pytest

from tidy.cli import main


@pytest.fixture
def messy(downloads: Path, make_file) -> Path:
    make_file(downloads / "Jane Doe CV.pdf", b"%PDF")
    make_file(downloads / "song.mp3", b"ID3")
    make_file(downloads / "Setup.exe", b"MZ")
    return downloads


def test_plan_is_dry_run(messy: Path, capsys) -> None:
    before = sorted(p.name for p in messy.iterdir())
    assert main(["plan", str(messy)]) == 0
    out = capsys.readouterr().out
    assert "Documents/Career/Jane Doe CV.pdf" in out
    assert "Dry run" in out
    assert sorted(p.name for p in messy.iterdir()) == before


def test_plan_defaults_to_sandboxed_downloads(messy: Path, capsys) -> None:
    assert main(["plan", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {Path(r["source"]).name for r in rows} == {"Jane Doe CV.pdf", "song.mp3", "Setup.exe"}


def test_organise_requires_confirmation(messy: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["organise", str(messy)]) == 2
    assert (messy / "song.mp3").exists()


def test_organise_history_undo_roundtrip(messy: Path, capsys) -> None:
    assert main(["organise", str(messy), "--yes"]) == 0
    assert (messy / "Media" / "Audio" / "song.mp3").exists()
    capsys.readouterr()
    assert main(["history", "-v"]) == 0
    assert "completed" in capsys.readouterr().out
    assert main(["undo", "1", "--yes"]) == 0
    assert (messy / "song.mp3").exists()
    assert not (messy / "Media").exists()


def test_duplicates_command(messy: Path, make_file, capsys) -> None:
    make_file(messy / "copy.mp3", b"ID3")
    assert main(["duplicates", str(messy)]) == 0
    out = capsys.readouterr().out
    assert "2 copies" in out and "never deletes" in out
    assert (messy / "copy.mp3").exists()


def test_bad_path(tmp_path: Path, capsys) -> None:
    assert main(["plan", str(tmp_path / "nope")]) == 2
