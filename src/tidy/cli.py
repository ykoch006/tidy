"""Command-line interface: ``tidy-cli plan|organise|duplicates|history|undo``.

Dry run is the default everywhere; ``organise`` only moves files after the
plan is shown and the user confirms (or passes ``--yes``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from tidy import __version__
from tidy.core.categories import CategoryTree
from tidy.core.content_analyser import ContentAnalyser
from tidy.core.duplicates import DuplicateFinder
from tidy.core.history import Journal
from tidy.core.learning import LearnedRules
from tidy.core.organiser import Organiser
from tidy.core.pipeline import PipelineConfig, PipelineResult, run_pipeline
from tidy.database import Database
from tidy.models import MoveStatus
from tidy.settings import AppSettings
from tidy.utils.formatting import human_size, percent
from tidy.utils.logging import configure_logging
from tidy.utils.paths import data_dir, default_downloads_dir


def _open_db() -> Database:
    return Database(data_dir() / "tidy.sqlite3")


def _plan(db: Database, root: Path, args: argparse.Namespace) -> PipelineResult:
    settings = AppSettings.load(db)
    categories = CategoryTree(settings.category_overrides, settings.custom_categories)
    config = PipelineConfig(
        categories=categories,
        threshold=settings.confidence_threshold,
        include_subdirectories=args.subfolders or settings.include_subdirectories,
        include_hidden=settings.include_hidden,
        excluded_dirs=frozenset(Journal(db).all_created_dirs()),
    )
    content = None if args.no_content or not settings.analyse_contents else ContentAnalyser()
    return run_pipeline(root, config, learned=LearnedRules(db), content=content)


def _print_plan(result: PipelineResult, as_json: bool) -> None:
    plan = result.plan
    if as_json:
        rows = [
            {
                "source": str(m.item.path),
                "destination": str(m.destination),
                "category": m.classification.category,
                "confidence": round(m.classification.confidence, 3),
                "status": m.status.value,
                "reasons": m.classification.reasons,
                "note": m.note,
            }
            for m in plan.moves
        ]
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    for move in plan.moves:
        cls = move.classification
        if move.status is MoveStatus.PROPOSED:
            target = move.destination.relative_to(plan.root).as_posix()
        else:
            target = f"({move.status.value})"
        print(f"{percent(cls.confidence):>4}  {move.item.rel_path.as_posix()}  ->  {target}")
        print(f"      {cls.explain()}")
    counts = Counter(m.classification.category for m in plan.actionable())
    print()
    print(f"{len(plan.actionable())} moves proposed ({human_size(plan.total_size)}) in {plan.root}")
    for category, count in counts.most_common():
        print(f"  {count:>5}  {category}")
    if result.stats.skipped:
        print(
            f"{len(result.stats.skipped)} items skipped for safety (links, system or "
            "incomplete files)."
        )
    print("Dry run: nothing has been moved.")


def cmd_plan(args: argparse.Namespace) -> int:
    with_db = _open_db()
    try:
        _print_plan(_plan(with_db, args.path, args), args.json)
    finally:
        with_db.close()
    return 0


def cmd_organise(args: argparse.Namespace) -> int:
    db = _open_db()
    try:
        result = _plan(db, args.path, args)
        _print_plan(result, as_json=False)
        moves = result.plan.actionable()
        if not moves:
            print("Nothing to do.")
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print("Refusing to move files without confirmation; pass --yes.", file=sys.stderr)
                return 2
            answer = input(f"\nMove {len(moves)} items now? Type 'yes' to continue: ")
            if answer.strip().lower() != "yes":
                print("Cancelled. Nothing was moved.")
                return 1
        outcome = Organiser(Journal(db)).execute(result.plan)
        print(
            f"\n{outcome.status.value}: moved {outcome.done}, failed {outcome.failed}, "
            f"skipped {outcome.skipped}. Undo with: tidy-cli undo {outcome.operation_id}"
        )
        for message in outcome.messages:
            print(f"  - {message}")
        return 0 if not outcome.failed else 3
    finally:
        db.close()


def cmd_duplicates(args: argparse.Namespace) -> int:
    db = _open_db()
    try:
        report = DuplicateFinder(db).find(args.path)
    finally:
        db.close()
    for group in report.groups:
        print(
            f"{len(group.files)} copies, {human_size(group.size)} each "
            f"({human_size(group.wasted)} reclaimable)  sha256 {group.sha256[:12]}"
        )
        for ref in group.files:
            print(f"    {ref.path}")
    print(
        f"\n{len(report.groups)} groups, {human_size(report.wasted_bytes)} reclaimable. "
        "Tidy never deletes files."
    )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    db = _open_db()
    try:
        journal = Journal(db)
        for op in journal.operations(args.limit):
            print(
                f"#{op.id:<4} {op.created_at}  {op.status.value:<17} moved {op.done:>5} "
                f"of {op.total:<5} {op.root}"
            )
            if args.verbose:
                for move in journal.moves(op.id):
                    print(f"        {move.status.value:<12} {move.source} -> {move.destination}")
    finally:
        db.close()
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    db = _open_db()
    try:
        journal = Journal(db)
        op = journal.operation(args.operation)
        if op is None:
            print(f"No operation #{args.operation}", file=sys.stderr)
            return 2
        if not op.can_undo:
            print(f"Operation #{op.id} cannot be undone (status: {op.status.value}).")
            return 1
        if not args.yes:
            if not sys.stdin.isatty():
                print("Pass --yes to confirm.", file=sys.stderr)
                return 2
            if input(f"Restore {op.done} items? Type 'yes': ").strip().lower() != "yes":
                return 1
        result = Organiser(journal).undo(op.id)
        print(f"{result.status.value}: restored {result.done}, failed {result.failed}")
        for message in result.messages:
            print(f"  - {message}")
        return 0 if not result.failed else 3
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tidy-cli", description="Safely organise a folder.")
    parser.add_argument("--version", action="version", version=f"tidy {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_path(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "path",
            nargs="?",
            type=Path,
            default=None,
            help="folder to organise (default: your Downloads folder)",
        )
        p.add_argument(
            "--subfolders",
            action="store_true",
            help="organise files inside sub-folders individually",
        )
        p.add_argument("--no-content", action="store_true", help="don't read file contents")

    p = sub.add_parser("plan", help="show the proposed organisation (dry run)")
    add_path(p)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("organise", aliases=["organize"], help="plan, confirm, then move files")
    add_path(p)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_organise)

    p = sub.add_parser("duplicates", help="report identical files (never deletes)")
    p.add_argument("path", nargs="?", type=Path, default=None)
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("history", help="list past operations")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-v", "--verbose", action="store_true", help="list every move")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("undo", help="undo an operation by number")
    p.add_argument("operation", type=int)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_undo)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(data_dir() / "logs")
    args = build_parser().parse_args(argv)
    if hasattr(args, "path"):
        args.path = (args.path or default_downloads_dir()).expanduser()
        if not args.path.is_dir():
            print(f"Not a folder: {args.path}", file=sys.stderr)
            return 2
    try:
        db = _open_db()
        try:
            recovered = Organiser(Journal(db)).recover()
        finally:
            db.close()
        if recovered.needed:
            print("Recovered an interrupted operation:", *recovered.resolved, sep="\n  ")
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Completed moves are journaled; run `tidy-cli history`.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
