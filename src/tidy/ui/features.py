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
