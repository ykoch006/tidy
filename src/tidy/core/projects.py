"""Recognise directories that are coherent projects and must be moved whole."""

from __future__ import annotations

import os
from pathlib import Path

# Marker file/dir names (case-insensitive) → project type.
_MARKER_NAMES: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "pipfile": "python",
    "environment.yml": "python",
    "package.json": "web",
    "angular.json": "web",
    "vite.config.js": "web",
    "vite.config.ts": "web",
    "next.config.js": "web",
    "cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "cmakelists.txt": "cpp",
    "ableton project info": "music",
    "composer.json": "web",
    "gemfile": "code",
    ".git": "code",
}
# Marker extensions → project type.
_MARKER_EXTENSIONS: dict[str, str] = {
    ".sln": "dotnet",
    ".csproj": "dotnet",
    ".als": "music",  # Ableton Live set
    ".flp": "music",  # FL Studio
    ".rpp": "music",  # REAPER
    ".ptx": "music",  # Pro Tools
    ".cpr": "music",  # Cubase
    ".aup3": "music",  # Audacity
    ".logicx": "music",
    ".band": "music",
    ".xcodeproj": "code",
    ".uproject": "code",
    ".ipynb": "python",
}
# Loose code files: a folder that is mostly these is a project even without markers.
CODE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "web",
    ".ts": "web",
    ".tsx": "web",
    ".jsx": "web",
    ".html": "web",
    ".css": "web",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "java",
    ".cs": "dotnet",
    ".c": "cpp",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rb": "code",
    ".php": "web",
    ".swift": "code",
}
# More specific types win when several markers are present.
_PRIORITY = ["music", "dotnet", "rust", "go", "java", "cpp", "python", "web", "code"]


def detect_project(directory: Path) -> str | None:
    """Return a project type for ``directory`` or ``None``.

    Only the directory's own entries are inspected (one ``scandir``), so this
    is cheap enough to call on every folder during a scan.
    """
    found: set[str] = set()
    code_hits: dict[str, int] = {}
    total_files = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                lower = entry.name.lower()
                if lower in _MARKER_NAMES:
                    found.add(_MARKER_NAMES[lower])
                ext = os.path.splitext(lower)[1]
                if ext in _MARKER_EXTENSIONS:
                    found.add(_MARKER_EXTENSIONS[ext])
                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if is_file:
                    total_files += 1
                    if ext in CODE_EXTENSIONS:
                        lang = CODE_EXTENSIONS[ext]
                        code_hits[lang] = code_hits.get(lang, 0) + 1
    except OSError:
        return None

    if found:
        # A bare .git folder says "code"; refine it with the dominant language.
        if found == {"code"} and code_hits:
            return max(code_hits, key=code_hits.__getitem__)
        return min(found, key=_PRIORITY.index)
    code_total = sum(code_hits.values())
    if code_total >= 3 and code_total >= 0.6 * total_files:
        return max(code_hits, key=code_hits.__getitem__)
    return None
