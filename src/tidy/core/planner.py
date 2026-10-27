"""Turn classifications into a proposed move plan (dry run).

The planner never touches the filesystem except to *read* what already
exists, so building a plan is always safe. Every destination is validated to
stay inside the organised root, never pass through a link, and never collide
with an existing file or another planned move.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path, PurePath

from tidy.core.categories import REVIEW, CategoryTree
from tidy.models import Classification, FileItem, MoveStatus, Plan, PlannedMove, Source
from tidy.utils import paths
from tidy.utils.formatting import percent
from tidy.utils.paths import UnsafePathError


class Planner:
    def __init__(self, categories: CategoryTree, threshold: float = 0.55) -> None:
        self.categories = categories
        self.threshold = threshold

    # ------------------------------------------------------------------ #
    def build(
        self,
        root: Path,
        items: list[FileItem],
        classifications: list[Classification],
        groups: dict[int, str] | None = None,
    ) -> Plan:
        if len(items) != len(classifications):
            raise ValueError("items and classifications differ in length")
        groups = groups or {}
        plan = Plan(root=root)
        for index, (item, cls) in enumerate(zip(items, classifications, strict=True)):
            if cls.confidence < self.threshold and cls.category != REVIEW:
                cls.reasons.append(
                    f"confidence {percent(cls.confidence)} is below your "
                    f"{percent(self.threshold)} threshold — suggested {cls.category}"
                )
                cls = Classification(REVIEW, cls.confidence, cls.source, cls.reasons)
            plan.moves.append(PlannedMove(item=item, classification=cls, destination=item.path,
                                          group_id=groups.get(index)))
        self.recompute(plan)
        return plan

    # ------------------------------------------------------------------ #
    def folder_for(self, plan: Plan, move: PlannedMove) -> Path:
        if move.custom_folder is not None:
            return plan.root / move.custom_folder
        category = self.categories.resolve(move.classification.category)
        return plan.root / category.parts

    def recompute(self, plan: Plan) -> None:
        """(Re)assign collision-free destinations for every move, in order."""
        reserved: list[Path] = []
        root = plan.root
        for move in plan.moves:
            move.note = ""
            item = move.item
            try:
                folder = self.folder_for(plan, move)
                paths.ensure_within(root, folder)
                paths.assert_no_links_between(root, folder)
            except UnsafePathError as exc:
                move.status = MoveStatus.SKIPPED
                move.approved = False
                move.destination = item.path
                move.note = f"unsafe destination: {exc}"
                continue

            if _same(item.path.parent, folder):
                move.status = MoveStatus.UNCHANGED
                move.destination = item.path
                move.note = "already in the right folder"
                continue
            if _is_inside(folder, item.path):
                move.status = MoveStatus.SKIPPED
                move.approved = False
                move.destination = item.path
                move.note = "cannot move a folder into itself"
                continue

            wanted = folder / item.name
            # Unapproved moves don't reserve names, so they never push approved
            # moves to "(1)" names; approving re-runs this and resolves clashes.
            destination = paths.unique_destination(wanted, reserved)
            if destination != wanted:
                move.note = f"renamed to '{destination.name}' — '{item.name}' already exists there"
            move.destination = destination
            move.status = MoveStatus.PROPOSED
            if move.approved:
                reserved.append(destination)

    # -- edits from the UI --------------------------------------------- #
    def set_category(self, plan: Plan, move: PlannedMove, category: str) -> None:
        if category not in self.categories:
            raise ValueError(f"Unknown category: {category}")
        move.classification = Classification(
            category, 1.0, Source.USER, ["you chose this category"]
        )
        move.custom_folder = None
        move.edited = True
        move.approved = True
        self.recompute(plan)

    def set_custom_folder(self, plan: Plan, move: PlannedMove, folder_text: str) -> None:
        """Point a move at a user-typed relative folder. Raises ``UnsafePathError``."""
        folder = paths.parse_relative_folder(folder_text)
        if folder == PurePath():
            raise UnsafePathError("Choose a folder inside the organised folder")
        paths.ensure_within(plan.root, plan.root / folder)
        move.custom_folder = folder
        move.edited = True
        move.approved = True
        self.recompute(plan)

    @staticmethod
    def summary(plan: Plan) -> Counter[str]:
        return Counter(m.classification.category for m in plan.moves if not m.is_noop)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
        os.path.abspath(b)
    )


def _is_inside(candidate: Path, container: Path) -> bool:
    cand = Path(os.path.normcase(os.path.abspath(candidate)))
    cont = Path(os.path.normcase(os.path.abspath(container)))
    return cand == cont or cand.is_relative_to(cont)
