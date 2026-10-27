"""The configurable category tree.

Categories are identified by stable dotted keys (``documents.university``);
the folder each key maps to can be renamed or disabled in Settings without
breaking learned rules or cached AI answers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Any

from tidy.utils.paths import UnsafePathError, parse_relative_folder

REVIEW = "review"
OTHER = "other"


@dataclass(frozen=True, slots=True)
class Category:
    key: str
    folder: str  # relative, "/"-separated
    label: str
    description: str = ""
    enabled: bool = True

    @property
    def parts(self) -> PurePath:
        return parse_relative_folder(self.folder)

    @property
    def top_level(self) -> str:
        return self.parts.parts[0] if self.parts.parts else ""


DEFAULT_CATEGORIES: tuple[Category, ...] = (
    Category("documents.university", "Documents/University", "University",
             "Coursework, lecture notes, assignments, papers"),
    Category("documents.career", "Documents/Career", "Career", "CVs, cover letters, job offers"),
    Category("documents.finance", "Documents/Finance", "Finance",
             "Invoices, receipts, statements, tax"),
    Category("documents.personal", "Documents/Personal", "Personal",
             "IDs, tickets, bookings, insurance, medical"),
    Category("documents.general", "Documents", "Documents", "Other documents and spreadsheets"),
    Category("images.screenshots", "Images/Screenshots", "Screenshots", "Screen captures"),
    Category("images.photos", "Images/Photos", "Photos", "Camera and phone photos"),
    Category("images.artwork", "Images/Artwork", "Artwork",
             "Design files, illustrations, vector art"),
    Category("images.general", "Images", "Images", "Other images"),
    Category("media.audio", "Media/Audio", "Audio", "Music and sound files"),
    Category("media.video", "Media/Video", "Video", "Videos and subtitles"),
    Category("software.installers", "Software/Installers", "Installers",
             "Setup programs, packages, disk images"),
    Category("projects.python", "Projects/Python", "Python projects", "Python code and notebooks"),
    Category("projects.web", "Projects/Web", "Web projects", "HTML/JS/TS projects"),
    Category("projects.music", "Projects/Music", "Music projects", "DAW project files"),
    Category("projects.code", "Projects/Code", "Other code", "Other programming projects"),
    Category("archives", "Archives", "Archives", "ZIP, RAR, 7z and other archives"),
    Category(OTHER, "Other", "Other", "Recognised but miscellaneous files"),
    Category(REVIEW, "Review", "Needs review", "Low-confidence items for you to check"),
)  # fmt: skip

# Map from scanner project types to category keys.
PROJECT_CATEGORY = {
    "python": "projects.python",
    "web": "projects.web",
    "music": "projects.music",
}


def project_category(project_type: str) -> str:
    return PROJECT_CATEGORY.get(project_type, "projects.code")


class CategoryTree:
    """Default categories plus the user's renames, disabled entries and additions."""

    def __init__(
        self,
        overrides: dict[str, dict[str, Any]] | None = None,
        custom: list[dict[str, str]] | None = None,
    ) -> None:
        cats: dict[str, Category] = {c.key: c for c in DEFAULT_CATEGORIES}
        for item in custom or []:
            key = str(item.get("key", "")).strip()
            folder = str(item.get("folder", "")).strip()
            if not key or key in cats or not folder:
                continue
            cats[key] = Category(key, folder, item.get("label") or folder.split("/")[-1],
                                 item.get("description", ""))
        for key, change in (overrides or {}).items():
            if key not in cats:
                continue
            cat = cats[key]
            if "folder" in change and change["folder"]:
                cat = replace(cat, folder=str(change["folder"]))
            if "enabled" in change and key not in (REVIEW, OTHER):
                cat = replace(cat, enabled=bool(change["enabled"]))
            cats[key] = cat
        # Validate every folder up-front so a bad setting can never produce
        # an unsafe destination later.
        for key, cat in list(cats.items()):
            try:
                if not cat.parts.parts:
                    raise UnsafePathError("empty folder")
            except UnsafePathError:
                default = next((c for c in DEFAULT_CATEGORIES if c.key == key), None)
                if default is None:
                    del cats[key]
                else:
                    cats[key] = replace(default, enabled=cat.enabled)
        self._cats = cats

    def __contains__(self, key: object) -> bool:
        return key in self._cats

    def get(self, key: str) -> Category:
        return self._cats[key]

    def all(self) -> list[Category]:
        return list(self._cats.values())

    def enabled(self) -> list[Category]:
        return [c for c in self._cats.values() if c.enabled]

    def keys(self) -> list[str]:
        return [c.key for c in self.enabled()]

    def resolve(self, key: str) -> Category:
        """Return the category for ``key``, falling back through disabled ones.

        ``documents.university`` → ``documents.general`` → ``other``.
        """
        seen = set()
        while key not in seen:
            seen.add(key)
            cat = self._cats.get(key)
            if cat is not None and cat.enabled:
                return cat
            head = key.split(".", 1)[0]
            key = f"{head}.general" if f"{head}.general" != key and "." in key else OTHER
        return self._cats[OTHER]

    def top_level_names(self) -> frozenset[str]:
        return frozenset(c.top_level for c in self._cats.values() if c.top_level)
