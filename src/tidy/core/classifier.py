"""The hybrid classification pipeline.

Order of precedence for each item:

1. Learned rules from the user's own corrections (most trusted).
2. Deterministic extension and filename rules.
3. Content analysis, which may refine or override a rule when it is more
   confident (a PDF whose text is clearly a CV).

Optional AI classification runs afterwards, in batch, only for items still
below the confidence threshold (see :mod:`tidy.ai`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from typing import Protocol

from tidy.core.categories import CategoryTree
from tidy.core.learning import LearnedRules
from tidy.core.rules import classify_by_rules
from tidy.models import Classification, FileItem, Source
from tidy.utils.logging import get_logger

log = get_logger(__name__)


class ContentLayer(Protocol):
    def analyse(self, item: FileItem, current: Classification) -> Classification | None:
        """Return a better classification, or ``None`` if contents add nothing."""
        ...


class Classifier:
    def __init__(
        self,
        categories: CategoryTree,
        learned: LearnedRules | None = None,
        content: ContentLayer | None = None,
    ) -> None:
        self.categories = categories
        self.learned = learned
        self.content = content

    def classify(self, item: FileItem) -> Classification:
        result = self._classify(item)
        return self._normalise(result)

    def _classify(self, item: FileItem) -> Classification:
        if self.learned is not None:
            rule = self.learned.match(item.name)
            if rule is not None and rule.category in self.categories:
                return Classification(
                    rule.category,
                    0.95,
                    Source.LEARNED,
                    [f"you previously filed names like '{rule.pattern}' here"],
                )

        result = classify_by_rules(item)
        if self.content is None or item.is_folder:
            return result
        try:
            refined = self.content.analyse(item, result)
        except Exception:  # content parsers handle hostile input; never let one break a scan
            log.warning(
                "Content analysis failed", extra={"ctx": {"path": str(item.path)}}, exc_info=True
            )
            return result
        if refined is None:
            return result
        if refined.category == result.category:
            return Classification(
                result.category,
                max(result.confidence, refined.confidence),
                refined.source if refined.confidence > result.confidence else result.source,
                result.reasons + refined.reasons,
            )
        if refined.confidence >= result.confidence:
            return Classification(
                refined.category,
                refined.confidence,
                refined.source,
                refined.reasons + [f"(rules suggested {result.category})"],
            )
        return result

    def _normalise(self, result: Classification) -> Classification:
        """Map disabled or unknown categories onto an enabled fallback."""
        resolved = self.categories.resolve(result.category)
        if resolved.key != result.category:
            result.reasons.append(f"'{result.category}' is disabled; using '{resolved.key}'")
            result.category = resolved.key
        return result

    def classify_many(
        self,
        items: Iterable[FileItem],
        cancel: threading.Event | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> list[Classification]:
        out: list[Classification] = []
        for index, item in enumerate(items, 1):
            if cancel is not None and cancel.is_set():
                break
            out.append(self.classify(item))
            if progress and index % 50 == 0:
                progress(index)
        return out
