from __future__ import annotations

import os
from pathlib import Path

import pytest

from tidy.core.categories import REVIEW, CategoryTree
from tidy.core.pipeline import PipelineConfig, run_pipeline
from tidy.core.planner import Planner
from tidy.models import Classification, FileItem, ItemKind, MoveStatus, Source
from tidy.utils.paths import UnsafePathError


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            p = Path(dirpath) / f
            st = p.stat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
        out[str(Path(dirpath).relative_to(root)) + "/"] = (0, 0)
    return out


@pytest.fixture
def messy(downloads: Path, make_file) -> Path:
    make_file(downloads / "Jane Doe CV.pdf", b"%PDF")
    make_file(downloads / "Screenshot 2026-01-01 101010.png", b"png")
    make_file(downloads / "IMG_20250101_101010.jpg", b"jpg")
    make_file(downloads / "Setup.exe", b"MZ")
    make_file(downloads / "song.mp3", b"ID3")
    make_file(downloads / "mystery.qqq", b"?")
    make_file(downloads / "report.pdf", b"%PDF")
    make_file(downloads / "myapp" / "pyproject.toml", b"[project]")
    make_file(downloads / "myapp" / "app.py", b"print(1)")
    return downloads


def plan_for(root: Path, threshold: float = 0.55):
    return run_pipeline(root, PipelineConfig(CategoryTree(), threshold)).plan


def test_dry_run_changes_nothing(messy: Path) -> None:
    before = snapshot(messy)
    plan_for(messy)
    assert snapshot(messy) == before


def test_plan_destinations(messy: Path) -> None:
    plan = plan_for(messy)
    dest = {m.item.name: m.destination.relative_to(messy) for m in plan.moves}
    assert dest["Jane Doe CV.pdf"] == Path("Documents/Career/Jane Doe CV.pdf")
    assert dest["Screenshot 2026-01-01 101010.png"].parent == Path("Images/Screenshots")
    assert dest["IMG_20250101_101010.jpg"].parent == Path("Images/Photos")
    assert dest["Setup.exe"].parent == Path("Software/Installers")
    assert dest["song.mp3"].parent == Path("Media/Audio")
    assert dest["myapp"] == Path("Projects/Python/myapp")
    assert dest["mystery.qqq"].parent == Path("Review")
    review = next(m for m in plan.moves if m.item.name == "mystery.qqq")
    assert any("below your" in r for r in review.classification.reasons)
    assert all(m.status is MoveStatus.PROPOSED for m in plan.moves)
    assert all(m.approved for m in plan.moves)


def test_threshold_controls_review(messy: Path) -> None:
    plan = plan_for(messy, threshold=0.99)
    assert all(m.classification.category == REVIEW for m in plan.moves)


def test_collision_with_existing_file(messy: Path, make_file) -> None:
    make_file(messy / "Documents" / "report.pdf", b"existing")
    plan = plan_for(messy)
    move = next(m for m in plan.moves if m.item.name == "report.pdf")
    assert move.destination == messy / "Documents" / "report (1).pdf"
    assert "already exists" in move.note


def test_collision_between_planned_moves(downloads: Path, make_file) -> None:
    make_file(downloads / "a" / "notes.txt", b"1")
    make_file(downloads / "b" / "notes.txt", b"2")
    make_file(downloads / "c" / "NOTES.txt", b"3")
    result = run_pipeline(downloads, PipelineConfig(CategoryTree(), include_subdirectories=True))
    dests = sorted(m.destination.name for m in result.plan.moves)
    assert dests == ["NOTES (2).txt", "notes (1).txt", "notes.txt"]


def test_already_organised_is_unchanged(downloads: Path, make_file) -> None:
    make_file(downloads / "Media" / "Audio" / "song.mp3")
    # Category roots are skipped at the top level, so scan recursively from a
    # wrapper to prove the planner itself recognises an in-place item.
    planner = Planner(CategoryTree())
    item = FileItem(downloads / "Media" / "Audio" / "song.mp3", downloads, 1, 0)
    cls = Classification("media.audio", 0.9, Source.RULE, [])
    plan = planner.build(downloads, [item], [cls])
    assert plan.moves[0].status is MoveStatus.UNCHANGED
    assert plan.actionable() == []


def test_edit_category_and_custom_folder(messy: Path) -> None:
    planner = Planner(CategoryTree())
    plan = plan_for(messy)
    move = next(m for m in plan.moves if m.item.name == "report.pdf")
    planner.set_category(plan, move, "documents.finance")
    assert move.destination == messy / "Documents" / "Finance" / "report.pdf"
    assert move.classification.source is Source.USER
    planner.set_custom_folder(plan, move, "Documents\\Taxes/2026")
    assert move.destination == messy / "Documents" / "Taxes" / "2026" / "report.pdf"
    for bad in ["../outside", "C:\\Windows", "", "a/../../b"]:
        with pytest.raises(UnsafePathError):
            planner.set_custom_folder(plan, move, bad)
    with pytest.raises(ValueError):
        planner.set_category(plan, move, "not.a.category")


def test_unapproved_moves_do_not_reserve_names(downloads: Path, make_file) -> None:
    make_file(downloads / "x" / "a.txt")
    make_file(downloads / "y" / "a.txt")
    result = run_pipeline(downloads, PipelineConfig(CategoryTree(), include_subdirectories=True))
    plan = result.plan
    planner = Planner(CategoryTree())
    plan.moves[0].approved = False
    planner.recompute(plan)
    assert plan.moves[1].destination.name == "a.txt"


def test_folder_into_itself_is_skipped(downloads: Path, make_file) -> None:
    make_file(downloads / "Stuff" / "x.txt")
    planner = Planner(CategoryTree())
    item = FileItem(downloads / "Stuff", downloads, 1, 0, kind=ItemKind.FOLDER)
    plan = planner.build(downloads, [item], [Classification("other", 0.9, Source.RULE, [])])
    planner.set_custom_folder(plan, plan.moves[0], "Stuff/Inner")
    assert plan.moves[0].status is MoveStatus.SKIPPED
    assert not plan.moves[0].approved


def test_summary_counts(messy: Path) -> None:
    summary = Planner.summary(plan_for(messy))
    assert summary["projects.python"] == 1
    assert sum(summary.values()) == 8
