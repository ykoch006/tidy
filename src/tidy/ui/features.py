"""Wire optional features (content analysis, duplicates, AI) into the UI.

Kept separate so the core window works even if an optional dependency is
missing; each feature degrades to "not available" instead of crashing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tidy.utils.logging import get_logger

if TYPE_CHECKING:
    from tidy.ui.context import AppContext
    from tidy.ui.main_window import MainWindow

log = get_logger(__name__)


def install_features(ctx: AppContext, window: MainWindow) -> None:
    """Register optional layers and pages. Each one is isolated from the others."""
    for installer in (_install_content, _install_duplicates):
        try:
            installer(ctx, window)
        except Exception:
            log.exception(
                "Optional feature failed to load", extra={"ctx": {"feature": installer.__name__}}
            )


def _install_content(ctx: AppContext, window: MainWindow) -> None:
    from tidy.core.content_analyser import ContentAnalyser

    ctx.content_layer_factory = lambda settings: ContentAnalyser()


def _install_duplicates(ctx: AppContext, window: MainWindow) -> None:
    import os

    from tidy.ui.pages.duplicates import DuplicatesPage

    page = DuplicatesPage(ctx)
    window.duplicates = page  # type: ignore[attr-defined]
    window.add_page("duplicates", "Duplicates", page)

    def on_report(kind: str, report: object) -> None:
        if kind != "duplicates":
            return
        copies: dict[str, int] = {}
        for group in getattr(report, "groups", []) or []:
            for ref in group.files:
                copies[os.path.normcase(str(ref.path))] = len(group.files) - 1
        window.model.set_duplicates(copies)

    ctx.extra_results.connect(on_report)
    # Moving files makes an existing report stale.
    ctx.execute_started.connect(lambda _n: page.cancel())
    ctx.execute_finished.connect(lambda _r: page.clear())
