"""Layer 1: deterministic rule-based classification.

Extensions give a base category; filename keyword patterns refine it within a
compatible family (a "receipt" PNG stays an image, a "receipt" PDF becomes a
finance document). Every decision carries human-readable reasons.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from tidy.core.categories import OTHER, project_category
from tidy.models import Classification, FileItem, Source

# --------------------------------------------------------------------------- #
# Extension table: extension → (category, confidence, family)
# Families gate which keyword rules may refine a match.
# --------------------------------------------------------------------------- #
DOC, IMG, AUDIO, VIDEO, SOFT, ARCH, CODE, MISC = (
    "document", "image", "audio", "video", "software", "archive", "code", "misc"
)  # fmt: skip

_EXT: dict[str, tuple[str, float, str]] = {}


def _register(category: str, confidence: float, family: str, *exts: str) -> None:
    for ext in exts:
        _EXT[ext] = (category, confidence, family)


# fmt: off
_register("documents.general", 0.6, DOC,
          ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".tex", ".pages",
          ".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".numbers",
          ".ppt", ".pptx", ".odp", ".key", ".epub", ".mobi", ".djvu", ".xps", ".oxps")
_register("images.general", 0.6, IMG,
          ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".jfif")
_register("images.photos", 0.85, IMG,
          ".heic", ".heif", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf")
_register("images.artwork", 0.85, IMG,
          ".psd", ".ai", ".svg", ".kra", ".xcf", ".procreate", ".clip", ".sketch", ".fig",
          ".afdesign", ".afphoto", ".eps", ".blend", ".ico")
_register("media.audio", 0.85, AUDIO,
          ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aif", ".aiff",
          ".mid", ".midi", ".alac", ".amr")
_register("media.video", 0.85, VIDEO,
          ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".flv", ".mpg", ".mpeg",
          ".3gp", ".ts", ".mts")
_register("media.video", 0.6, VIDEO, ".srt", ".vtt", ".ass", ".ssa", ".sub")
_register("software.installers", 0.8, SOFT,
          ".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".dmg", ".pkg",
          ".deb", ".rpm", ".apk", ".appimage", ".flatpakref", ".vsix")
_register("software.installers", 0.7, SOFT, ".iso", ".img")
_register("archives", 0.75, ARCH,
          ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".lz")
_register("projects.python", 0.6, CODE, ".py", ".pyw")
_register("projects.python", 0.75, CODE, ".ipynb")
_register("projects.web", 0.55, CODE,
          ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx", ".php", ".vue")
_register("projects.code", 0.55, CODE,
          ".c", ".cpp", ".h", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".swift",
          ".sh", ".ps1", ".bat", ".sql", ".r", ".m", ".lua", ".scala", ".dart")
_register("projects.music", 0.9, AUDIO,
          ".als", ".flp", ".rpp", ".ptx", ".cpr", ".aup3", ".logicx", ".band", ".sf2", ".nki")
_register(OTHER, 0.7, MISC,
          ".ttf", ".otf", ".woff", ".woff2", ".torrent", ".ics", ".vcf", ".url", ".lnk",
          ".gpx", ".kml", ".stl", ".obj", ".3mf", ".gcode")
_register(OTHER, 0.45, MISC,
          ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".log", ".dat", ".bin", ".db",
          ".sqlite")
# fmt: on


@dataclass(frozen=True, slots=True)
class KeywordRule:
    category: str
    families: frozenset[str]
    pattern: re.Pattern[str]
    confidence: float
    reason: str


def _kw(
    category: str, families: set[str], regex: str, confidence: float, reason: str
) -> KeywordRule:
    return KeywordRule(
        category, frozenset(families), re.compile(regex, re.IGNORECASE), confidence, reason
    )


KEYWORD_RULES: tuple[KeywordRule, ...] = (
    # Screenshots before photos: "Screenshot_20240101_120000.png" looks like both.
    _kw(
        "images.screenshots",
        {IMG},
        r"^(screenshot|screen ?shot|screen capture|capture|snip|"
        r"scr \d|snapshot)|\bscreenshot\b",
        0.92,
        "filename looks like a screenshot",
    ),
    _kw(
        "images.photos",
        {IMG},
        r"^(img|dsc|dscn|dscf|pxl|p|gopr|dji|mvimg)[ _]?\d{3,}|"
        r"^\d{8}[ _]\d{6}|whatsapp image|^photo[ _-]",
        0.88,
        "camera-style filename",
    ),
    _kw(
        "images.artwork",
        {IMG},
        r"\b(wallpaper|artwork|illustration|drawing|sketch|render|"
        r"logo|icon|banner|poster|mockup)\b",
        0.75,
        "artwork keyword in filename",
    ),
    _kw(
        "documents.career",
        {DOC},
        r"\b(cv|resume|résumé|curriculum vitae|cover letter|"
        r"covering letter|job offer|offer letter|employment contract|reference letter|"
        r"portfolio|interview)\b",
        0.88,
        "career keyword in filename",
    ),
    _kw(
        "documents.finance",
        {DOC, IMG},
        r"\b(invoice|receipt|statement|payslip|pay slip|"
        r"payroll|tax|p60|p45|w-?2|1099|bank|mortgage|bill|order confirmation|quote|budget|"
        r"expenses?|vat)\b",
        0.85,
        "finance keyword in filename",
    ),
    _kw(
        "documents.university",
        {DOC},
        r"\b(assignment|coursework|lecture|tutorial|seminar|"
        r"module|syllabus|dissertation|thesis|essay|homework|exam|revision|lab report|"
        r"problem sheet|worksheet|cw ?\d|week ?\d{1,2}|lab ?\d{1,2}|"
        r"[a-z]{2,5}\d{3,4}[a-z]?)\b",
        0.8,
        "academic keyword in filename",
    ),
    _kw(
        "documents.personal",
        {DOC, IMG},
        r"\b(passport|driving licen[cs]e|birth certificate|"
        r"visa|insurance|medical|prescription|boarding pass|e-?ticket|ticket|booking|"
        r"reservation|tenancy|lease)\b",
        0.82,
        "personal-document keyword in filename",
    ),
    _kw(
        "software.installers",
        {SOFT},
        r"\b(setup|installer|install|x64|x86|win64|win32|amd64|"
        r"arm64|portable)\b",
        0.92,
        "installer keyword in filename",
    ),
    _kw("media.video", {VIDEO}, r"\b(screen ?recording|screencast)\b", 0.9, "screen recording"),
)

_SEPARATORS = re.compile(r"[_\-.+]+")


def normalise_name(name: str) -> str:
    """Lowercase stem with separators turned into spaces, for keyword matching."""
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    return _SEPARATORS.sub(" ", stem).strip().lower()


def extension_info(ext: str) -> tuple[str, float, str] | None:
    return _EXT.get(ext.lower())


FAMILY_LABELS = {
    DOC: "documents", IMG: "images", AUDIO: "audio", VIDEO: "video", SOFT: "software",
    ARCH: "archives", CODE: "code", MISC: "miscellaneous files",
}  # fmt: skip


def describe_extension(ext: str, family: str) -> str:
    return f"{ext.lstrip('.').upper()} files are usually {FAMILY_LABELS.get(family, family)}"


def family_of(category: str) -> str:
    head = category.split(".", 1)[0]
    return {
        "documents": DOC, "images": IMG, "media": AUDIO if category == "media.audio" else VIDEO,
        "software": SOFT, "archives": ARCH, "projects": CODE,
    }.get(head, MISC)  # fmt: skip


def classify_file_by_rules(item: FileItem) -> Classification:
    ext = item.extension
    info = extension_info(ext)
    norm = normalise_name(item.name)
    if info is None:
        reason = f"unknown file type '{ext}'" if ext else "file has no extension"
        return Classification(OTHER, 0.3, Source.FALLBACK, [reason])

    category, confidence, family = info
    reasons = [describe_extension(ext, family)]
    for rule in KEYWORD_RULES:
        if family in rule.families and rule.pattern.search(norm):
            if rule.confidence > confidence or rule.category != category:
                category, confidence = rule.category, max(rule.confidence, confidence)
                reasons.append(rule.reason)
            break
    return Classification(category, confidence, Source.RULE, reasons)


def classify_folder_by_rules(item: FileItem) -> Classification:
    """Classify a folder unit by project markers, else by its dominant contents."""
    if item.project_type:
        return Classification(
            project_category(item.project_type),
            0.95,
            Source.RULE,
            [f"folder is a {item.project_type} project"],
        )
    if item.file_count == 0:
        return Classification(OTHER, 0.3, Source.FALLBACK, ["empty folder"])

    # Vote with the sampled file names (so "DSC0001.JPG" counts as a photo) and
    # fall back to extension counts for the files that were not sampled.
    votes: Counter[str] = Counter()
    for name in item.sample_names:
        sample = FileItem(path=item.path / name, root=item.root, size=0, mtime_ns=0)
        votes[classify_file_by_rules(sample).category] += 1
    unsampled = max(0, item.file_count - len(item.sample_names))
    if unsampled or not votes:
        for ext, count in item.ext_counts.items():
            info = extension_info(ext)
            votes[info[0] if info else OTHER] += count * unsampled / max(1, item.file_count)
    category, top = votes.most_common(1)[0]
    share = top / max(1e-9, sum(votes.values()))
    # Refine using the folder name (e.g. "Lecture notes" full of PDFs).
    family = family_of(category)
    norm = normalise_name(item.name + ".")
    label = (
        FAMILY_LABELS.get(family, family)
        if category.endswith(".general")
        else (category.split(".")[-1])
    )
    reasons = [f"{share:.0%} of its {item.file_count} files look like {label}"]
    for rule in KEYWORD_RULES:
        if family in rule.families and rule.pattern.search(norm):
            category = rule.category
            reasons.append(rule.reason.replace("filename", "folder name"))
            break
    confidence = 0.4 + 0.45 * share if share >= 0.5 else 0.35
    return Classification(category, round(confidence, 3), Source.RULE, reasons)


def classify_by_rules(item: FileItem) -> Classification:
    return classify_folder_by_rules(item) if item.is_folder else classify_file_by_rules(item)
