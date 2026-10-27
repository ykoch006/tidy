from __future__ import annotations

from pathlib import Path

import pytest

from tidy.core.categories import OTHER, CategoryTree
from tidy.core.classifier import Classifier
from tidy.core.grouping import apply_groups, base_stem, find_groups
from tidy.core.learning import LearnedRules, generalise
from tidy.core.rules import classify_by_rules
from tidy.models import Classification, FileItem, ItemKind, Source

ROOT = Path("/virtual/root")


def item(name: str, size: int = 10) -> FileItem:
    return FileItem(path=ROOT / name, root=ROOT, size=size, mtime_ns=0)


def folder(name: str, project: str | None = None, exts: dict[str, int] | None = None) -> FileItem:
    exts = exts or {}
    return FileItem(
        path=ROOT / name,
        root=ROOT,
        size=100,
        mtime_ns=0,
        kind=ItemKind.FOLDER,
        project_type=project,
        file_count=sum(exts.values()),
        ext_counts=exts,
    )


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("report.docx", "documents.general"),
        ("Screenshot 2026-03-01 at 10.22.01.png", "images.screenshots"),
        ("Screenshot_20240101_120000.png", "images.screenshots"),
        ("IMG_20240512_101010.jpg", "images.photos"),
        ("DSC01234.JPG", "images.photos"),
        ("PXL_20230101_000000123.jpg", "images.photos"),
        ("holiday.heic", "images.photos"),
        ("poster.psd", "images.artwork"),
        ("cool wallpaper.png", "images.artwork"),
        ("song.mp3", "media.audio"),
        ("movie.mkv", "media.video"),
        ("movie.en.srt", "media.video"),
        ("Firefox Setup 130.0.exe", "software.installers"),
        ("python-3.12.10-amd64.exe", "software.installers"),
        ("ubuntu-24.04.iso", "software.installers"),
        ("archive.7z", "archives"),
        ("script.py", "projects.python"),
        ("analysis.ipynb", "projects.python"),
        ("beat.flp", "projects.music"),
        ("Jane Doe CV.pdf", "documents.career"),
        ("resume_2026.docx", "documents.career"),
        ("Cover Letter - Acme.pdf", "documents.career"),
        ("Invoice_2023_04.pdf", "documents.finance"),
        ("amazon receipt.png", "documents.finance"),
        ("bank statement march.pdf", "documents.finance"),
        ("COMP1234 Assignment 2.pdf", "documents.university"),
        ("Lecture 5 - Graphs.pptx", "documents.university"),
        ("week 3 notes.docx", "documents.university"),
        ("Passport scan.pdf", "documents.personal"),
        ("boarding pass LHR.pdf", "documents.personal"),
        ("font.ttf", OTHER),
        ("data.torrent", OTHER),
    ],
)
def test_rule_categories(name: str, category: str) -> None:
    result = classify_by_rules(item(name))
    assert result.category == category, result.reasons
    assert result.reasons


def test_unknown_extension_is_low_confidence() -> None:
    result = classify_by_rules(item("mystery.qqq"))
    assert result.category == OTHER
    assert result.confidence < 0.5
    assert result.source is Source.FALLBACK
    assert classify_by_rules(item("README")).confidence < 0.5


def test_receipt_image_stays_document_family_aware() -> None:
    # A "statement" video is still a video — keyword families gate refinement.
    assert classify_by_rules(item("statement.mp4")).category == "media.video"


def test_scan_pdf_not_mistaken_for_course_code() -> None:
    assert classify_by_rules(item("scan 0001.pdf")).category == "documents.general"


def test_folder_projects_and_dominant_content() -> None:
    assert classify_by_rules(folder("tidy", "python")).category == "projects.python"
    assert classify_by_rules(folder("game", "rust")).category == "projects.code"
    photos = classify_by_rules(folder("Holiday 2025", exts={".jpg": 40, ".mp4": 2}))
    assert photos.category == "images.general"
    assert photos.confidence > 0.75
    lectures = classify_by_rules(folder("Lecture slides", exts={".pdf": 10}))
    assert lectures.category == "documents.university"
    mixed = classify_by_rules(folder("stuff", exts={".pdf": 1, ".mp3": 1, ".zip": 1, ".exe": 1}))
    assert mixed.confidence < 0.5
    assert classify_by_rules(folder("empty")).confidence < 0.5


def test_category_tree_overrides_and_fallback() -> None:
    tree = CategoryTree(
        overrides={
            "documents.university": {"enabled": False},
            "images.photos": {"folder": "Pictures/Camera"},
            "review": {"enabled": False},  # cannot disable the safety net
        }
    )
    assert tree.resolve("documents.university").key == "documents.general"
    assert tree.get("images.photos").folder == "Pictures/Camera"
    assert tree.resolve("review").key == "review"
    assert tree.resolve("nonsense.key").key == OTHER
    assert "Pictures" in tree.top_level_names()


def test_category_tree_rejects_unsafe_folder_override() -> None:
    tree = CategoryTree(overrides={"archives": {"folder": "../../Windows"}})
    assert tree.get("archives").folder == "Archives"
    tree = CategoryTree(custom=[{"key": "work", "folder": "C:\\evil"}])
    assert "work" not in tree


def test_custom_category() -> None:
    tree = CategoryTree(
        custom=[{"key": "work.clients", "folder": "Work/Clients", "label": "Clients"}]
    )
    assert tree.get("work.clients").top_level == "Work"
    assert tree.resolve("work.clients").key == "work.clients"


def test_learning_generalises_and_overrides(db) -> None:
    assert generalise("Invoice_2023_04.pdf") == "invoice_*_*.pdf"
    learned = LearnedRules(db)
    learned.learn("Invoice_2023_04.pdf", "documents.personal")
    classifier = Classifier(CategoryTree(), learned)
    result = classifier.classify(item("INVOICE_2024_11.pdf"))
    assert result.category == "documents.personal"
    assert result.source is Source.LEARNED
    # unrelated names are unaffected
    assert classifier.classify(item("invoice-final.pdf")).category == "documents.finance"
    rule = learned.all()[0]
    learned.remove(rule.id)
    assert classifier.classify(item("INVOICE_2024_11.pdf")).category == "documents.finance"


def test_learning_brackets_are_literal(db) -> None:
    learned = LearnedRules(db)
    learned.learn("[draft] plan.docx", "documents.career")
    assert learned.match("[draft] plan.docx") is not None
    assert learned.match("d plan.docx") is None


def test_classifier_maps_disabled_category() -> None:
    tree = CategoryTree(overrides={"images.screenshots": {"enabled": False}})
    result = Classifier(tree).classify(item("Screenshot 1.png"))
    assert result.category == "images.general"


class FakeContent:
    def __init__(self, result: Classification | None) -> None:
        self.result = result

    def analyse(self, item, current):
        return self.result


def test_content_layer_overrides_only_when_more_confident() -> None:
    tree = CategoryTree()
    strong = FakeContent(
        Classification("documents.career", 0.9, Source.CONTENT, ["looks like a CV"])
    )
    assert Classifier(tree, content=strong).classify(item("doc.pdf")).category == "documents.career"
    weak = FakeContent(Classification("documents.career", 0.3, Source.CONTENT, ["meh"]))
    assert Classifier(tree, content=weak).classify(item("doc.pdf")).category == "documents.general"


def test_content_layer_crash_is_contained() -> None:
    class Boom:
        def analyse(self, item, current):
            raise ValueError("corrupt file")

    result = Classifier(CategoryTree(), content=Boom()).classify(item("x.pdf"))
    assert result.category == "documents.general"


def test_base_stem() -> None:
    assert base_stem("Report (1).pdf") == "report"
    assert base_stem("movie.en.srt") == "movie"
    assert base_stem("photo - Copy.jpg") == "photo"
    assert base_stem("v1.2 notes.pdf") == "v1.2 notes"


def test_grouping_sidecars_follow_primary() -> None:
    items = [item("film.mkv"), item("film.en.srt"), item("song.mp3"), item("song.lrc")]
    cls = [classify_by_rules(i) for i in items]
    membership = apply_groups(items, cls)
    assert cls[1].category == "media.video"
    assert cls[3].category == "media.audio"
    assert cls[3].source is Source.GROUP
    assert membership[0] == membership[1]
    assert membership[2] != membership[0]


def test_grouping_same_stem_documents_unify() -> None:
    items = [item("COMP1234 Coursework.docx"), item("COMP1234 Coursework.pdf"), item("other.pdf")]
    cls = [classify_by_rules(i) for i in items]
    cls[1] = Classification("documents.general", 0.6, Source.RULE, ["pdf"])
    apply_groups(items, cls)
    assert cls[1].category == "documents.university"
    assert cls[2].category == "documents.general"


def test_grouping_does_not_merge_unrelated_families() -> None:
    items = [item("project.zip"), item("project.pdf")]
    cls = [classify_by_rules(i) for i in items]
    apply_groups(items, cls)
    assert cls[0].category == "archives"
    assert cls[1].category == "documents.general"


def test_series_grouping() -> None:
    items = [item(f"Lecture {n}.pdf") for n in range(1, 5)] + [
        item("IMG_1.jpg"),
        item("IMG_2.jpg"),
        item("IMG_3.jpg"),
    ]
    groups = find_groups(items)
    assert len(groups) == 1
    assert groups[0].kind == "series"
    assert sorted(groups[0].indices) == [0, 1, 2, 3]
