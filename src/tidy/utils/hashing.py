"""Streaming file hashing."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

CHUNK = 1024 * 1024
PARTIAL_BYTES = 64 * 1024


class HashCancelled(Exception):
    pass


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise HashCancelled()
            digest.update(chunk)
    return digest.hexdigest()


def partial_hash(path: Path) -> str:
    """Hash of the first and last 64 KB — a cheap pre-filter for duplicates."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        digest.update(fh.read(PARTIAL_BYTES))
        fh.seek(0, 2)
        size = fh.tell()
        if size > PARTIAL_BYTES:
            fh.seek(max(PARTIAL_BYTES, size - PARTIAL_BYTES))
            digest.update(fh.read(PARTIAL_BYTES))
    return digest.hexdigest()
