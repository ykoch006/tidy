"""High-level "scan → classify → group → plan" pipeline used by the UI and CLI."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tidy.core.categories import CategoryTree
from tidy.core.classifier import Classifier, ContentLayer
from tidy.core.grouping import apply_groups
from tidy.core.learning import LearnedRules
from tidy.core.planner import Planner
from tidy.core.scanner import Scanner, ScanOptions, ScanStats
from tidy.models import Classification, FileItem, Plan

# (phase, done, total-or-0, message)
PipelineProgress = Callable[[str, int, int, str], None]


@dataclass
class PipelineResult:
    root: Path
    items: list[FileItem]
    classifications: list[Classification]
    plan: Plan
    stats: ScanStats
    groups: dict[int, str] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    categories: CategoryTree
    threshold: float = 0.55
    include_subdirectories: bool = False
    include_hidden: bool = False
    excluded_dirs: frozenset[Path] = frozenset()


def run_pipeline(
    root: Path,
    config: PipelineConfig,
    learned: LearnedRules | None = None,
    content: ContentLayer | None = None,
    cancel: threading.Event | None = None,
    progress: PipelineProgress | None = None,
    post_classify: Callable[[list[FileItem], list[Classification]], None] | None = None,
) -> PipelineResult:
    """Run the full dry-run pipeline. Nothing on disk is modified."""
    cancel = cancel or threading.Event()

    def report(phase: str, done: int, total: int, message: str = "") -> None:
        if progress:
            progress(phase, done, total, message)

    options = ScanOptions(
        include_subdirectories=config.include_subdirectories,
        include_hidden=config.include_hidden,
        excluded_dirs=config.excluded_dirs,
        reserved_top_level=config.categories.top_level_names(),
    )
    scanner = Scanner(
        options, cancel,
        progress=lambda st, p: report("scan", st.files + st.folders, 0, p.name),
    )
    report("scan", 0, 0, str(root))
    items = list(scanner.scan(root))
    report("scan", len(items), len(items), "done")

    classifier = Classifier(config.categories, learned, content)
    total = len(items)
    classifications = classifier.classify_many(
        items, cancel, progress=lambda n: report("classify", n, total, "")
    )
    if cancel.is_set():
        from tidy.core.scanner import ScanCancelled

        raise ScanCancelled()
    report("classify", total, total, "done")

    if post_classify is not None:  # e.g. optional AI refinement of low-confidence items
        post_classify(items, classifications)

    groups = apply_groups(items, classifications)
    plan = Planner(config.categories, config.threshold).build(root, items, classifications, groups)
    report("plan", total, total, "done")
    return PipelineResult(root, items, classifications, plan, scanner.stats, groups)
