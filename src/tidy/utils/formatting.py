"""Human-friendly formatting helpers."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(num_bytes: int | float) -> str:
    """Format a byte count using binary multiples (1 KB = 1024 B)."""
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def human_time(timestamp: float) -> str:
    """Format a POSIX timestamp as local date and time."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def percent(value: float) -> str:
    """Format a 0..1 confidence as a percentage."""
    return f"{round(value * 100):d}%"
