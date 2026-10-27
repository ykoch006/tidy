"""Safety tests for executing, undoing and recovering organisation operations.

All files live in pytest temporary directories.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from tidy.core import fileops
from tidy.core.categories import CategoryTree
from tidy.core.history import Journal, MoveState, OpStatus
from tidy.core.organiser import Organiser
from tidy.core.pipeline import PipelineConfig, run_pipeline
from tidy.core.planner import Planner
from tidy.models import Plan


def tree(root: Path) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


@pytest.fixture
def journal(db) -> Journal:
    return Journal(db)


@pytest.fixture
def messy(downloads: Path, make_file) -> Path:
    make_file(downloads / "Jane Doe CV.pdf", b"%PDF cv")
    make_file(downloads / "Screenshot 1.png", b"png-bytes")
    make_file(downloads / "song.mp3", b"ID3 audio")
    make_file(downloads / "Setup.exe", b"MZ installer")
    make_file(downloads / "app" / "pyproject.toml", b"[project]")
    make_file(downloads / "app" / "src" / "main.py", b"print('hi')")
    return downloads


def plan_for(root: Path, **kw) -> Plan:
    return run_pipeline(root, PipelineConfig(CategoryTree(), **kw)).plan


def force_cross_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fileops, "same_volume", lambda a, b: False)


# --------------------------------------------------------------------------- #
# Execute
# --------------------------------------------------------------------------- #


def test_execute_moves_everything_and_preserves_content(messy: Path, journal: Journal) -> None:
    before = tree(messy)
    plan = plan_for(messy)
    result = Organiser(journal).execute(plan)
    assert result.status is OpStatus.COMPLETED
    assert result.done == 5
    after = tree(messy)
    assert sorted(after.values()) == sorted(before.values()), "content must be preserved"
    assert after["Documents/Career/Jane Doe CV.pdf"] == b"%PDF cv"
    assert after["Projects/Python/app/src/main.py"] == b"print('hi')"
    assert not (messy / "song.mp3").exists()


def test_execute_preserves_mtime(messy: Path, journal: Journal, make_file) -> None:
    make_file(messy / "old.mp3", b"old", mtime=1_000_000_000)
    plan = plan_for(messy)
    Organiser(journal).execute(plan)
    assert (messy / "Media/Audio/old.mp3").stat().st_mtime == pytest.approx(1_000_000_000)


def test_only_approved_moves_execute(messy: Path, journal: Journal) -> None:
    plan = plan_for(messy)
    for m in plan.moves:
        m.approved = m.item.name == "song.mp3"
    result = Organiser(journal).execute(plan)
    assert result.done == 1
    assert (messy / "Jane Doe CV.pdf").exists()
    assert (messy / "Media/Audio/song.mp3").exists()


def test_never_overwrites_file_created_after_planning(
    messy: Path, journal: Journal, make_file
) -> None:
    plan = plan_for(messy)
    make_file(messy / "Media" / "Audio" / "song.mp3", b"someone else's song")
    result = Organiser(journal).execute(plan)
    assert result.status is OpStatus.COMPLETED
    assert (messy / "Media/Audio/song.mp3").read_bytes() == b"someone else's song"
    assert (messy / "Media/Audio/song (1).mp3").read_bytes() == b"ID3 audio"
    assert any("avoid overwriting" in m for m in result.messages)


def test_changed_source_is_skipped(messy: Path, journal: Journal) -> None:
    plan = plan_for(messy)
    target = messy / "song.mp3"
    target.write_bytes(b"ID3 audio - edited and longer")
    result = Organiser(journal).execute(plan)
    assert result.status is OpStatus.PARTIAL
    assert result.skipped == 1
    assert target.exists(), "changed file must stay put"
    rec = next(m for m in journal.moves(result.operation_id) if m.source == target)
    assert rec.status is MoveState.SKIPPED
    assert "changed" in (rec.error or "")


def test_deleted_source_is_skipped(messy: Path, journal: Journal) -> None:
    plan = plan_for(messy)
    (messy / "song.mp3").unlink()
    result = Organiser(journal).execute(plan)
    assert result.skipped == 1 and result.done == 4


def test_changed_folder_is_skipped(messy: Path, journal: Journal, make_file) -> None:
    plan = plan_for(messy)
    make_file(messy / "app" / "new_file.py", b"x = 1")
    result = Organiser(journal).execute(plan)
    assert (messy / "app" / "new_file.py").exists()
    assert result.skipped == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking semantics")
def test_locked_file_fails_safely(messy: Path, journal: Journal) -> None:
    plan = plan_for(messy)
    with open(messy / "song.mp3", "rb"):
        result = Organiser(journal).execute(plan)
    assert result.failed == 1
    assert (messy / "song.mp3").read_bytes() == b"ID3 audio"
    assert result.status is OpStatus.PARTIAL


def test_read_only_file_moves(messy: Path, journal: Journal) -> None:
    os.chmod(messy / "song.mp3", stat.S_IREAD)
    try:
        result = Organiser(journal).execute(plan_for(messy))
        assert result.done == 5
    finally:
        moved = messy / "Media/Audio/song.mp3"
        if moved.exists():
            os.chmod(moved, stat.S_IWRITE | stat.S_IREAD)


def test_destination_through_junction_rejected(
    messy: Path, tmp_path: Path, journal: Journal
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    plan = plan_for(messy)
    media = messy / "Media"
    if sys.platform == "win32":
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(media), str(outside)], check=True, capture_output=True
        )
    else:
        os.symlink(outside, media, target_is_directory=True)
    result = Organiser(journal).execute(plan)
    assert result.failed == 1
    assert list(outside.iterdir()) == [], "nothing may be written through the junction"
    assert (messy / "song.mp3").exists()


def test_tampered_destination_outside_root_rejected(
    messy: Path, tmp_path: Path, journal: Journal
) -> None:
    plan = plan_for(messy)
    plan.moves[0].destination = tmp_path / "escaped.bin"
    result = Organiser(journal).execute(plan)
    assert result.failed == 1
    assert not (tmp_path / "escaped.bin").exists()


def test_cancel_stops_remaining_moves(messy: Path, journal: Journal) -> None:
    cancel = threading.Event()
    calls = []

    def progress(done, total, name):
        calls.append(done)
        if done == 2:
            cancel.set()

    result = Organiser(journal, cancel, progress).execute(plan_for(messy))
    assert result.status is OpStatus.CANCELLED
    assert result.done == 2
    assert result.skipped == 3


def test_unusual_filenames_roundtrip(downloads: Path, journal: Journal, make_file) -> None:
    names = [
        "résumé (final).pdf",
        "日本語.mp3",
        "emoji 🎉.png",
        "a" * 150 + ".zip",
        "-dash.txt",
        "dots.in.name.tar.gz",
    ]
    for n in names:
        make_file(downloads / n, n.encode())
    before = tree(downloads)
    org = Organiser(journal)
    result = org.execute(plan_for(downloads))
    assert result.done == len(names)
    org.undo(result.operation_id)
    assert tree(downloads) == before


# --------------------------------------------------------------------------- #
# Cross-volume
# --------------------------------------------------------------------------- #


def test_cross_volume_file_and_folder_moves(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    before = tree(messy)
    result = Organiser(journal).execute(plan_for(messy))
    assert result.status is OpStatus.COMPLETED
    assert sorted(tree(messy).values()) == sorted(before.values())
    assert not (messy / "app").exists()
    assert not list(messy.rglob("*.tidy-partial"))


def test_cross_volume_verification_failure_keeps_original(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    real_copy = fileops.shutil.copy2

    def corrupting_copy(src, dst, *a, **k):
        real_copy(src, dst, *a, **k)
        if str(src).endswith("song.mp3"):
            Path(dst).write_bytes(b"corrupted")

    monkeypatch.setattr(fileops.shutil, "copy2", corrupting_copy)
    result = Organiser(journal).execute(plan_for(messy))
    assert result.failed == 1
    assert (messy / "song.mp3").read_bytes() == b"ID3 audio"
    assert not (messy / "Media" / "Audio" / "song.mp3").exists()
    assert not list(messy.rglob("*.tidy-partial"))


def test_cross_volume_source_removal_failure_leaves_single_copy(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    real_unlink = fileops._unlink

    def failing_unlink(path):
        if path.name == "song.mp3" and path.parent == messy:
            raise PermissionError(13, "in use")
        real_unlink(path)

    monkeypatch.setattr(fileops, "_unlink", failing_unlink)
    result = Organiser(journal).execute(plan_for(messy))
    assert result.failed == 1
    assert (messy / "song.mp3").exists()
    assert not (messy / "Media" / "Audio" / "song.mp3").exists()


def test_cross_volume_folder_with_link_refused(
    messy: Path, journal: Journal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = messy / "app" / "linked"
    if sys.platform == "win32":
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        os.symlink(outside, link, target_is_directory=True)
    result = Organiser(journal).execute(plan_for(messy))
    assert result.failed == 1
    assert (messy / "app" / "pyproject.toml").exists()


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #


def test_undo_restores_everything_and_removes_created_dirs(messy: Path, journal: Journal) -> None:
    before = tree(messy)
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    undo = org.undo(result.operation_id)
    assert undo.status is OpStatus.UNDONE
    assert undo.done == 5
    assert tree(messy) == before
    assert sorted(p.name for p in messy.iterdir()) == sorted(
        ["Jane Doe CV.pdf", "Screenshot 1.png", "song.mp3", "Setup.exe", "app"]
    )
    op = journal.operation(result.operation_id)
    assert op is not None and op.status is OpStatus.UNDONE and not op.can_undo


def test_undo_keeps_user_files_in_created_dirs(messy: Path, journal: Journal, make_file) -> None:
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    make_file(messy / "Media" / "Audio" / "added later.mp3", b"mine")
    org.undo(result.operation_id)
    assert (messy / "Media" / "Audio" / "added later.mp3").read_bytes() == b"mine"


def test_undo_never_overwrites_reoccupied_source(messy: Path, journal: Journal, make_file) -> None:
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    make_file(messy / "song.mp3", b"new download with same name")
    undo = org.undo(result.operation_id)
    assert (messy / "song.mp3").read_bytes() == b"new download with same name"
    assert (messy / "song (1).mp3").read_bytes() == b"ID3 audio"
    assert any("restored as" in m for m in undo.messages)


def test_undo_missing_item_is_reported(messy: Path, journal: Journal) -> None:
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    (messy / "Media" / "Audio" / "song.mp3").unlink()
    undo = org.undo(result.operation_id)
    assert undo.status is OpStatus.PARTIALLY_UNDONE
    assert undo.failed == 1 and undo.done == 4


def test_undo_cross_volume(messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch) -> None:
    before = tree(messy)
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    force_cross_volume(monkeypatch)
    org.undo(result.operation_id)
    assert tree(messy) == before


def test_history_survives_restart(messy: Path, tmp_path: Path) -> None:
    from tidy.database import Database

    path = tmp_path / "persist.sqlite3"
    db = Database(path)
    result = Organiser(Journal(db)).execute(plan_for(messy))
    db.close()
    db2 = Database(path)
    journal = Journal(db2)
    ops = journal.operations()
    assert ops[0].id == result.operation_id and ops[0].done == 5
    Organiser(journal).undo(result.operation_id)
    assert (messy / "song.mp3").exists()
    db2.close()


# --------------------------------------------------------------------------- #
# Interruption and recovery
# --------------------------------------------------------------------------- #


class Crash(BaseException):
    """Simulates the process dying (not caught as an ordinary error)."""


def _crash_on(monkeypatch: pytest.MonkeyPatch, target: str, name: str, after: bool = False):
    real = getattr(fileops, target)

    def wrapper(*args, **kwargs):
        first = Path(args[0])
        if first.name.startswith(name) and not after:
            raise Crash()
        result = real(*args, **kwargs)
        if first.name.startswith(name) and after:
            raise Crash()
        return result

    monkeypatch.setattr(fileops, target, wrapper)


def test_recovery_after_crash_before_rename(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_for(messy)
    _crash_on(monkeypatch, "rename_no_overwrite", "song.mp3")
    with pytest.raises(Crash):
        Organiser(journal).execute(plan)
    monkeypatch.undo()
    report = Organiser(journal).recover()
    assert report.needed
    assert (messy / "song.mp3").exists()
    ops = journal.operations()
    assert ops[0].status is OpStatus.RECOVERED
    states = {m.source.name: m.status for m in journal.moves(ops[0].id)}
    assert states["song.mp3"] is MoveState.FAILED
    assert MoveState.PENDING not in states.values()
    # Moves completed before the crash can still be undone.
    Organiser(journal).undo(ops[0].id)
    assert (messy / "Jane Doe CV.pdf").read_bytes() == b"%PDF cv"
    assert not (messy / "Documents").exists()


def test_recovery_after_cross_volume_copy_before_source_removed(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    plan = plan_for(messy)
    real = fileops.remove_original

    def crash_remove(src, dst, kind):
        if src.name == "song.mp3":
            raise Crash()
        return real(src, dst, kind)

    monkeypatch.setattr(fileops, "remove_original", crash_remove)
    with pytest.raises(Crash):
        Organiser(journal).execute(plan)
    # Both copies exist right now.
    assert (messy / "song.mp3").exists() and (messy / "Media/Audio/song.mp3").exists()
    monkeypatch.undo()
    Organiser(journal).recover()
    assert not (messy / "song.mp3").exists()
    assert (messy / "Media/Audio/song.mp3").read_bytes() == b"ID3 audio"
    rec = next(m for m in journal.moves(journal.operations()[0].id) if m.source.name == "song.mp3")
    assert rec.status is MoveState.DONE


def test_recovery_discards_partial_copy(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    force_cross_volume(monkeypatch)
    plan = plan_for(messy)

    def crash_mid_copy(src, dst, *a, **k):
        if Path(src).name == "song.mp3":
            Path(dst).write_bytes(b"ID3")  # half-written
            raise Crash()
        return fileops.shutil.copyfile(src, dst)

    monkeypatch.setattr(fileops, "_copy_file", crash_mid_copy)
    monkeypatch.setattr(fileops, "discard_temp", lambda p: None)  # simulate hard kill: no cleanup
    with pytest.raises(Crash):
        Organiser(journal).execute(plan)
    assert list(messy.rglob("*.tidy-partial"))
    monkeypatch.undo()
    Organiser(journal).recover()
    assert not list(messy.rglob("*.tidy-partial"))
    assert (messy / "song.mp3").read_bytes() == b"ID3 audio"


def test_recover_is_noop_when_clean(messy: Path, journal: Journal) -> None:
    Organiser(journal).execute(plan_for(messy))
    assert not Organiser(journal).recover().needed


def test_recovery_of_interrupted_undo(
    messy: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = Organiser(journal)
    result = org.execute(plan_for(messy))
    _crash_on(monkeypatch, "rename_no_overwrite", "song.mp3", after=True)
    with pytest.raises(Crash):
        Organiser(journal).undo(result.operation_id)
    monkeypatch.undo()
    Organiser(journal).recover()
    rec = next(m for m in journal.moves(result.operation_id) if m.source.name == "song.mp3")
    assert rec.status is MoveState.UNDONE
    assert (messy / "song.mp3").exists()
    # The rest can still be undone afterwards.
    Organiser(journal).undo(result.operation_id)
    assert (messy / "Jane Doe CV.pdf").exists()


def test_planner_edits_flow_into_execution(messy: Path, journal: Journal) -> None:
    plan = plan_for(messy)
    planner = Planner(CategoryTree())
    move = next(m for m in plan.moves if m.item.name == "Setup.exe")
    planner.set_custom_folder(plan, move, "Software/Tools")
    Organiser(journal).execute(plan)
    assert (messy / "Software" / "Tools" / "Setup.exe").exists()
