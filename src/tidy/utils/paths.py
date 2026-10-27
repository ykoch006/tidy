"""Platform locations and filesystem-safety primitives.

Everything that decides *whether a path is safe to touch* lives here so that it
can be audited and tested in one place.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import Iterable
from pathlib import Path, PurePath, PureWindowsPath

APP_DIR_NAME = "Tidy"

# Windows FILE_ATTRIBUTE_REPARSE_POINT: set for symlinks, junctions, OneDrive
# placeholders and other filter-driver objects we must never traverse.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_COMPONENT_LENGTH = 180

# Directory names that belong to the OS or other tools and are never entered.
PROTECTED_DIR_NAMES = frozenset(
    name.casefold()
    for name in (
        "$RECYCLE.BIN",
        "System Volume Information",
        "$WinREAgent",
        "WindowsApps",
        ".Trash",
        ".Trashes",
        ".Spotlight-V100",
        ".fseventsd",
        "lost+found",
    )
)
# Files that are OS metadata, not user content.
IGNORED_FILE_NAMES = frozenset(
    name.casefold() for name in ("desktop.ini", "thumbs.db", ".DS_Store", "Icon\r")
)


class UnsafePathError(ValueError):
    """Raised when a path would escape its allowed root or is otherwise unsafe."""


# --------------------------------------------------------------------------- #
# Application locations
# --------------------------------------------------------------------------- #


def data_dir() -> Path:
    """Return (and create) the per-user directory for Tidy's database and logs.

    ``TIDY_DATA_DIR`` overrides the location, which tests rely on to stay isolated.
    """
    override = os.environ.get("TIDY_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        base = base / APP_DIR_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_DIR_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _windows_known_downloads() -> Path | None:
    """Ask the Windows shell for FOLDERID_Downloads (handles redirected folders)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # {374DE290-123F-4565-9164-39C4925E467B}
    folder_id = GUID(
        0x374DE290,
        0x123F,
        0x4565,
        (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B),
    )
    path_ptr = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    except AttributeError:
        return None
    result = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr))
    try:
        if result != 0 or not path_ptr.value:
            return None
        return Path(path_ptr.value)
    finally:
        ole32.CoTaskMemFree(path_ptr)


def _xdg_downloads() -> Path | None:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^XDG_DOWNLOAD_DIR="([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        return None
    return Path(match.group(1).replace("$HOME", str(Path.home())))


def default_downloads_dir() -> Path:
    """Locate the user's Downloads folder.

    ``TIDY_DOWNLOADS_DIR`` overrides detection; development and tests point it at
    a synthetic sandbox so the real folder is never scanned by accident.
    """
    override = os.environ.get("TIDY_DOWNLOADS_DIR")
    if override:
        return Path(override)
    found: Path | None = None
    if sys.platform == "win32":
        found = _windows_known_downloads()
    elif sys.platform.startswith("linux"):
        found = _xdg_downloads()
    return found or Path.home() / "Downloads"


# --------------------------------------------------------------------------- #
# Link / junction / protected detection
# --------------------------------------------------------------------------- #


def is_link_like(path: Path, st: os.stat_result | None = None) -> bool:
    """True for symlinks, NTFS junctions and any other reparse point."""
    try:
        if path.is_symlink() or path.is_junction():
            return True
        st = st or path.lstat()
    except OSError:
        return True  # if we cannot inspect it, treat it as unsafe
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def is_protected_dir_name(name: str) -> bool:
    return name.casefold() in PROTECTED_DIR_NAMES


def is_ignored_file_name(name: str) -> bool:
    return name.casefold() in IGNORED_FILE_NAMES


def is_hidden_or_system(path: Path, st: os.stat_result | None = None) -> bool:
    """Dot-files and Windows hidden/system files."""
    if path.name.startswith("."):
        return True
    try:
        st = st or path.lstat()
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return (
        bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM)) if attrs else False
    )


# --------------------------------------------------------------------------- #
# Name sanitising and destination validation
# --------------------------------------------------------------------------- #


def sanitize_component(name: str) -> str:
    """Turn arbitrary text into a single filesystem-safe path component.

    Used for names derived from untrusted sources (e.g. AI suggestions or
    metadata). Valid user filenames pass through unchanged.
    """
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    if cleaned in {"", ".", ".."}:
        cleaned = "_"
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > MAX_COMPONENT_LENGTH:
        suffix = Path(cleaned).suffix[:20]
        cleaned = cleaned[: MAX_COMPONENT_LENGTH - len(suffix)] + suffix
    return cleaned


def validate_component(name: str) -> None:
    """Reject (rather than repair) a component a user typed."""
    if name in {"", ".", ".."}:
        raise UnsafePathError(f"Invalid folder or file name: {name!r}")
    if _INVALID_CHARS.search(name):
        raise UnsafePathError(f"Name contains characters not allowed on Windows: {name!r}")
    if name != name.rstrip(". "):
        raise UnsafePathError(f"Name must not end with a dot or space: {name!r}")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise UnsafePathError(f"{name!r} is a reserved Windows device name")
    if len(name) > 255:
        raise UnsafePathError("Name is longer than 255 characters")


def parse_relative_folder(text: str) -> PurePath:
    """Parse a user-supplied relative folder such as ``Documents/University``.

    Both ``/`` and ``\\`` are accepted as separators. Absolute paths, drive
    letters, UNC prefixes and ``..`` traversal are rejected.
    """
    text = text.strip()
    if not text:
        return PurePath()
    win = PureWindowsPath(text)
    if win.drive or win.root or text.startswith(("/", "\\")):
        raise UnsafePathError(f"Destination must be relative to the organised folder: {text!r}")
    parts = [p for p in re.split(r"[\\/]+", text) if p != ""]
    for part in parts:
        validate_component(part)
    return PurePath(*parts)


def ensure_within(root: Path, candidate: Path) -> Path:
    """Return the resolved ``candidate`` if it lies inside ``root``; raise otherwise."""
    root_resolved = root.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise UnsafePathError(f"{candidate} escapes the allowed folder {root}")
    return resolved


def assert_no_links_between(root: Path, candidate: Path) -> None:
    """Refuse destinations whose existing ancestors (below ``root``) are links.

    A junction planted inside the organised folder could otherwise redirect a
    move to somewhere else on disk.
    """
    root = root.resolve(strict=False)
    try:
        rel = candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError(f"{candidate} is not inside {root}") from exc
    current = root
    for part in rel.parts:
        current = current / part
        if not os.path.lexists(current):
            return
        if is_link_like(current):
            raise UnsafePathError(f"{current} is a link or junction; refusing to write through it")


def unique_destination(target: Path, reserved: Iterable[Path] = ()) -> Path:
    """Return ``target`` or ``name (n).ext`` such that nothing exists or is reserved there.

    Comparison is case-insensitive because Windows filesystems are.
    """
    taken = {str(p).casefold() for p in reserved}

    def free(p: Path) -> bool:
        return not os.path.lexists(p) and str(p).casefold() not in taken

    if free(target):
        return target
    is_dir_like = target.suffix == "" or target.is_dir()
    stem, suffix = (target.name, "") if is_dir_like else (target.stem, target.suffix)
    for n in range(1, 10_000):
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if free(candidate):
            return candidate
    raise FileExistsError(f"Could not find a free name for {target}")


def same_volume(a: Path, b: Path) -> bool:
    """True if ``a`` and ``b`` (or their nearest existing ancestors) share a device."""

    def device(p: Path) -> int:
        p = p.resolve(strict=False)
        while not p.exists():
            if p.parent == p:
                break
            p = p.parent
        return p.stat().st_dev

    try:
        return device(a) == device(b)
    except OSError:
        return False
