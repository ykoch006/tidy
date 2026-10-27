from __future__ import annotations

from pathlib import Path

import pytest

from tidy.database import Database
from tidy.database.db import MIGRATIONS
from tidy.settings import AppSettings


def test_migrations_apply_once(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    db = Database(path)
    assert db.schema_version == len(MIGRATIONS)
    db.close()
    reopened = Database(path)  # must not try to re-run migrations
    assert reopened.schema_version == len(MIGRATIONS)
    reopened.close()


def test_settings_roundtrip(db: Database) -> None:
    settings = AppSettings.load(db)
    assert settings.ai_enabled is False, "AI must be opt-in"
    assert settings.ai_consent_content is False
    settings.confidence_threshold = 0.7
    settings.include_subdirectories = True
    settings.save(db)
    loaded = AppSettings.load(db)
    assert loaded.confidence_threshold == 0.7
    assert loaded.include_subdirectories is True


def test_settings_ignore_unknown_and_clamp(db: Database) -> None:
    db.set_setting("app_settings", {"confidence_threshold": 7, "bogus": 1})
    loaded = AppSettings.load(db)
    assert loaded.confidence_threshold == 1.0


def test_transaction_rolls_back(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('a', '1')")
        raise RuntimeError("boom")
    assert db.get_setting("a") is None
