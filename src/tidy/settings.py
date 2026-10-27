"""Typed application settings persisted in the SQLite settings table."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from tidy.database import Database

SETTINGS_KEY = "app_settings"
DEFAULT_AI_MODEL = "claude-opus-5-5"


@dataclass
class AppSettings:
    # Scanning
    downloads_dir: str | None = None
    include_subdirectories: bool = False
    include_hidden: bool = False
    analyse_contents: bool = True
    # Classification
    confidence_threshold: float = 0.55
    category_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    custom_categories: list[dict[str, str]] = field(default_factory=list)
    # Appearance
    theme: str = "dark"
    # Optional AI — everything off until the user opts in
    ai_enabled: bool = False
    ai_model: str = DEFAULT_AI_MODEL
    ai_consent_filenames: bool = False
    ai_consent_content: bool = False
    ai_instructions: str = ""
    ai_api_key: str = ""  # optional; ANTHROPIC_API_KEY in the environment is preferred
    ai_max_files_per_scan: int = 50

    @classmethod
    def load(cls, db: Database) -> AppSettings:
        raw = db.get_setting(SETTINGS_KEY, {}) or {}
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in raw.items() if k in known})
        settings.confidence_threshold = min(max(float(settings.confidence_threshold), 0.0), 1.0)
        return settings

    def save(self, db: Database) -> None:
        db.set_setting(SETTINGS_KEY, asdict(self))
