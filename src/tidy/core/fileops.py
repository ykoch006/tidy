"""Low-level, no-overwrite move primitives.

* Same volume: a single atomic rename that fails if the destination exists.
* Across volumes: copy to a ``.tidy-partial`` temp name next to the
  destination, verify SHA-256 of every byte, atomically rename the temp into
  place, and only then remove the original.

Nothing here ever deletes a user file that has not first been verified to
exist intact at its new location.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from tidy.core.scanner import summarise_folder
from tidy.models import ItemKind
from tidy.utils.hashing import sha256_file
from tidy.utils.logging import get_logger
from tidy.utils.paths import same_volume

log = get_logger(__name__)

TEMP_SUFFIX = ".tidy-partial"


class TransferError(OSError):
    """A move could not be completed; the original is untouched unless stated."""


def temp_path_for(dst: Path) -> Path:
    candidate = dst.with_name(dst.name + TEMP_SUFFIX)
    n = 1
    while os.path.lexists(candidate):
        candidate = dst.with_name(f"{dst.name}.{n}{TEMP_SUFFIX}")
        n += 1
    return candidate


def move_item(
    src: Path,
    dst: Path,
    kind: ItemKind,
    on_temp: Callable[[Path], None] | None = None,
) -> None:
    """Move ``src`` to ``dst``. ``dst`` must not exist; its parent must.

    ``on_temp`` is called with the temp path *before* a cross-volume copy
    starts, so the caller can journal it durably for crash recovery.
    """
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "Destination already exists", str(dst))
    if not dst.parent.is_dir():
        raise FileNotFoundError(errno.ENOENT, "Destination folder missing", str(dst.parent))
    if same_volume(src, dst.parent):
        rename_no_overwrite(src, dst)
    else:
        copy_verify_remove(src, dst, kind, on_temp)


def rename_no_overwrite(src: Path, dst: Path) -> None:
    if sys.platform == "win32":
        os.rename(src, dst)  # MoveFileEx without REPLACE_EXISTING: fails if dst exists
        return
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "Destination already exists", str(dst))
    if src.is_dir() and not src.is_symlink():
        os.rename(src, dst)
        return
    try:
        os.link(src, dst, follow_symlinks=False)  # atomic, fails if dst exists
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise
        os.rename(src, dst)  # filesystem without hard links (FAT, some network shares)
        return
    os.unlink(src)


# --------------------------------------------------------------------------- #
# Cross-volume
# --------------------------------------------------------------------------- #


def copy_verify_remove(
    src: Path, dst: Path, kind: ItemKind, on_temp: Callable[[Path], None] | None = None
) -> None:
    temp = temp_path_for(dst)
    if kind is ItemKind.FOLDER:
        summary = summarise_folder(src)
        if summary.link_count:
            raise TransferError(
                "Folder contains links or junctions and cannot be safely copied to another "
                "drive. Move it manually or choose a destination on the same drive."
            )
        if summary.unreadable:
            raise TransferError("Some sub-folders could not be read; nothing was moved.")
    if on_temp is not None:
        on_temp(temp)
    try:
        if kind is ItemKind.FOLDER:
            shutil.copytree(src, temp, symlinks=True, copy_function=_copy_file)
            verify_tree(src, temp)
        else:
            _copy_file(src, temp)
            if sha256_file(src) != sha256_file(temp):
                raise TransferError("Copied file failed verification; original kept.")
        rename_no_overwrite(temp, dst)
    except BaseException:
        discard_temp(temp)
        raise
    remove_original(src, dst, kind)


def _copy_file(src: str | Path, dst: str | Path) -> None:
    shutil.copy2(src, dst)
    preserve_creation_time(Path(src), Path(dst))


def verify_tree(src: Path, copy: Path) -> None:
    """Every file under ``src`` must exist under ``copy`` with identical bytes."""
    for dirpath, _dirs, files in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        for name in files:
            a = Path(dirpath) / name
            b = copy / rel / name
            if not b.is_file() or a.stat().st_size != b.stat().st_size:
                raise TransferError(f"Copy verification failed for {rel / name}; original kept.")
            if sha256_file(a) != sha256_file(b):
                raise TransferError(f"Copy verification failed for {rel / name}; original kept.")


def discard_temp(temp: Path) -> None:
    """Remove a partial copy that Tidy itself created."""
    try:
        if temp.is_dir() and not temp.is_symlink():
            shutil.rmtree(temp, onexc=_make_writable_and_retry)
        elif os.path.lexists(temp):
            _unlink(temp)
    except OSError:
        log.warning(
            "Could not remove partial copy", extra={"ctx": {"path": str(temp)}}, exc_info=True
        )


def remove_original(src: Path, dst: Path, kind: ItemKind) -> None:
    """Remove the original after a verified copy.

    For a single file this is all-or-nothing: if it fails, the verified copy is
    removed again so the file exists exactly once. For a folder a partial
    failure leaves both copies and is reported — data is never lost.
    """
    if kind is ItemKind.FOLDER:
        errors: list[str] = []

        def onexc(func, path, exc):  # type: ignore[no-untyped-def]
            try:
                _make_writable_and_retry(func, path, exc)
            except OSError as inner:
                errors.append(f"{path}: {inner}")

        shutil.rmtree(src, onexc=onexc)
        if errors:
            raise TransferError(
                "Copied to the new location, but some original files are in use and were left "
                f"in place ({len(errors)} item(s)). Nothing was lost."
            )
        return
    try:
        _unlink(src)
    except OSError as exc:
        discard_temp(dst)
        raise TransferError(f"Original could not be removed ({exc}); move cancelled.") from exc


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # clear Windows read-only attribute
        os.unlink(path)


def _make_writable_and_retry(func, path, _exc) -> None:  # type: ignore[no-untyped-def]
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


def preserve_creation_time(src: Path, dst: Path) -> None:
    """Copy the Windows creation timestamp (``shutil.copy2`` does not)."""
    if sys.platform != "win32":
        return
    try:
        birth = os.stat(src).st_birthtime
    except (OSError, AttributeError):
        return
    import ctypes
    from ctypes import wintypes

    filetime = int((birth + 11644473600) * 10_000_000)
    ft = wintypes.FILETIME(filetime & 0xFFFFFFFF, filetime >> 32)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    generic_write, open_existing, backup_semantics = 0x40000000, 3, 0x02000000
    handle = kernel32.CreateFileW(
        str(dst), generic_write, 0, None, open_existing, backup_semantics, None
    )
    if handle in (None, wintypes.HANDLE(-1).value):
        return
    try:
        kernel32.SetFileTime(handle, ctypes.byref(ft), None, None)
    finally:
        kernel32.CloseHandle(handle)
