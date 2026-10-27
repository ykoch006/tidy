"""Learned rules: user-approved category corrections saved as filename patterns.

When the user changes a file's category and approves the move, Tidy stores a
generalised glob of the filename (digits become ``*``), so
``Invoice_2023_04.pdf`` teaches ``invoice_*_*.pdf`` → Finance.
"""

from __future__ import annotations

import fnmatch
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from tidy.database import Database

_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class LearnedRule:
    id: int
    pattern: str
    category: str
    hits: int


def generalise(name: str) -> str:
    """Turn a filename into a reusable glob pattern."""
    pattern = _DIGITS.sub("*", name.lower())
    pattern = pattern.replace("[", "[[]")  # keep literal brackets literal for fnmatch
    return re.sub(r"\*+", "*", pattern)


class LearnedRules:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._cache: list[LearnedRule] | None = None

    def _load(self) -> list[LearnedRule]:
        with self._lock:
            if self._cache is None:
                rows = self._db.query(
                    "SELECT id, pattern, category, hits FROM user_rules "
                    "ORDER BY length(pattern) DESC"
                )
                self._cache = [
                    LearnedRule(r["id"], r["pattern"], r["category"], r["hits"]) for r in rows
                ]
            return self._cache

    def all(self) -> list[LearnedRule]:
        return list(self._load())

    def match(self, name: str) -> LearnedRule | None:
        """Return the most specific (longest) pattern that matches ``name``."""
        lower = name.lower()
        for rule in self._load():
            if fnmatch.fnmatchcase(lower, rule.pattern):
                return rule
        return None

    def learn(self, name: str, category: str) -> LearnedRule:
        pattern = generalise(name)
        now = datetime.now(UTC).isoformat()
        self._db.execute(
            "INSERT INTO user_rules(pattern, category, created_at) VALUES(?, ?, ?) "
            "ON CONFLICT(pattern) DO UPDATE SET category = excluded.category",
            (pattern, category, now),
        )
        self.invalidate()
        row = self._db.query_one("SELECT id, hits FROM user_rules WHERE pattern = ?", (pattern,))
        assert row is not None
        return LearnedRule(row["id"], pattern, category, row["hits"])

    def record_hit(self, rule_id: int) -> None:
        self._db.execute("UPDATE user_rules SET hits = hits + 1 WHERE id = ?", (rule_id,))

    def remove(self, rule_id: int) -> None:
        self._db.execute("DELETE FROM user_rules WHERE id = ?", (rule_id,))
        self.invalidate()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
