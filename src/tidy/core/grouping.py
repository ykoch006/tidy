"""Keep related files together.

Two kinds of relationship are recognised:

* **Same stem** – ``report.docx`` / ``report.pdf``, ``song.mp3`` / ``song.lrc``,
  ``IMG_0001.CR2`` / ``IMG_0001.xmp``, including browser copy suffixes like
  ``report (1).pdf``.
* **Series** – three or more files whose names differ only by numbers
  (``Lecture 1.pdf`` … ``Lecture 12.pdf``).

Within a group, members in the same family adopt the most confident member's
category; sidecar files (subtitles, lyrics, XMP…) always follow their primary.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from tidy.core.rules import family_of
from tidy.models import Classification, FileItem, Source

SIDECAR_EXTENSIONS = frozenset(
    {".srt", ".vtt", ".ass", ".ssa", ".sub", ".idx", ".lrc", ".cue", ".xmp", ".aae", ".thm",
     ".sfv", ".md5", ".sha256", ".nfo", ".sig", ".asc"}
)  # fmt: skip
_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|\s*-\s*copy(\s*\(\d+\))?|\s+copy(\s+\d+)?)$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
MIN_SERIES = 3


@dataclass(slots=True)
class Group:
    id: str
    indices: list[int]
    kind: str  # "stem" or "series"


def base_stem(name: str) -> str:
    """``Report (1).final.pdf`` → ``report (1).final`` minus copy suffix → ``report``-ish key."""
    stem = name
    # Strip *all* extensions for sidecars like movie.en.srt → movie
    while True:
        head, dot, ext = stem.rpartition(".")
        if not dot or not head or len(ext) > 5 or " " in ext:
            break
        stem = head
    return _COPY_SUFFIX.sub("", stem).strip().casefold()


def find_groups(items: list[FileItem]) -> list[Group]:
    stems: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        if item.is_folder:
            continue
        stems[(str(item.path.parent).casefold(), base_stem(item.name))].append(index)

    groups: list[Group] = []
    grouped: set[int] = set()
    for (_, stem), members in stems.items():
        if len(members) > 1:
            groups.append(Group(f"stem:{stem}", members, "stem"))
            grouped.update(members)

    series: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        if item.is_folder or index in grouped:
            continue
        stem = base_stem(item.name)
        if not _DIGITS.search(stem):
            continue
        key = _DIGITS.sub("#", stem)
        if len(key.replace("#", "").strip(" _-.")) < 4:
            continue  # "IMG_#" style keys are too generic to mean "belongs together"
        series[(str(item.path.parent).casefold(), key, item.extension)].append(index)
    for (_, key, ext), members in series.items():
        if len(members) >= MIN_SERIES:
            groups.append(Group(f"series:{key}{ext}", members, "series"))
    return groups


def apply_groups(
    items: list[FileItem], classifications: list[Classification]
) -> dict[int, str]:
    """Harmonise classifications in place; return ``{index: group_id}``."""
    membership: dict[int, str] = {}
    for group in find_groups(items):
        for i in group.indices:
            membership[i] = group.id
        primaries = [i for i in group.indices if items[i].extension not in SIDECAR_EXTENSIONS]
        leader = max(primaries or group.indices, key=lambda i: classifications[i].confidence)
        lead = classifications[leader]
        lead_family = family_of(lead.category)
        for i in group.indices:
            if i == leader:
                continue
            current = classifications[i]
            if current.category == lead.category:
                continue
            is_sidecar = items[i].extension in SIDECAR_EXTENSIONS
            same_family = family_of(current.category) == lead_family
            if (is_sidecar or same_family) and lead.confidence >= current.confidence:
                classifications[i] = Classification(
                    lead.category,
                    min(lead.confidence, 0.95),
                    Source.GROUP,
                    [f"kept with related file '{items[leader].name}'", *current.reasons[:1]],
                )
    return membership
