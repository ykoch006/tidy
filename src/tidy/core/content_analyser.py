"""Layer 2: local content analysis.

Reads a bounded amount of each supported file — the first pages of a PDF,
the first paragraphs of a Word document, a ZIP's file *listing*, an image's
EXIF/PNG metadata — and scores it against keyword profiles. Nothing leaves the
machine. Every reader is bounded in bytes, pages or entries, so a huge or
malicious file cannot stall a scan.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tidy.core.categories import project_category
from tidy.core.projects import CODE_EXTENSIONS
from tidy.core.rules import DOC, IMG, extension_info
from tidy.models import Classification, FileItem, Source
from tidy.utils.logging import get_logger

log = get_logger(__name__)

MAX_PARSE_BYTES = 150 * 1024 * 1024  # don't open documents bigger than this
MAX_TEXT_CHARS = 20_000
PDF_PAGES = 3
MAX_ZIP_ENTRIES = 5_000

# --------------------------------------------------------------------------- #
# Keyword profiles: (phrase regex, weight). Each phrase counts once.
# --------------------------------------------------------------------------- #
PROFILES: dict[str, list[tuple[str, float]]] = {
    "documents.career": [
        (r"curriculum vitae", 4), (r"work experience", 2.5), (r"employment history", 3),
        (r"professional experience", 2.5), (r"key skills", 2), (r"personal statement", 2),
        (r"references (are )?available", 2.5), (r"linkedin\.com/in", 2), (r"education", 0.8),
        (r"skills", 0.8), (r"career objective", 2), (r"dear hiring manager", 3),
        (r"i am writing to apply", 3), (r"(job|offer) of employment", 3), (r"starting salary", 2),
    ],
    "documents.university": [
        (r"coursework", 2.5), (r"assignment", 2), (r"module (code|leader|title)", 3),
        (r"student (id|number)", 3), (r"word count", 2), (r"learning outcomes", 2.5),
        (r"marking (criteria|scheme)", 3), (r"plagiarism", 2), (r"academic year", 2),
        (r"submission", 1.2), (r"deadline", 0.8), (r"lecturer", 1.5), (r"lecture", 1.2),
        (r"seminar", 1.2), (r"tutorial", 1), (r"university", 1.5), (r"faculty of", 1.5),
        (r"bibliography", 1.5), (r"et al\.?", 1), (r"abstract", 1), (r"dissertation", 2.5),
        (r"thesis", 2), (r"exam(ination)?", 1.2), (r"syllabus", 2.5),
    ],
    "documents.finance": [
        (r"invoice (no|number|#)", 3.5), (r"invoice", 2), (r"amount due", 3), (r"total due", 3),
        (r"subtotal", 2), (r"\bvat\b", 1.5), (r"receipt", 2.5), (r"account number", 2.5),
        (r"sort code", 2.5), (r"\biban\b", 2.5), (r"statement (period|date)", 3),
        (r"opening balance", 3), (r"closing balance", 3), (r"payslip", 3), (r"net pay", 3),
        (r"gross pay", 3), (r"order (number|confirmation)", 2), (r"billing address", 2),
        (r"payment (due|method|received)", 2), (r"tax year", 2.5),
    ],
    "documents.personal": [
        (r"passport", 3), (r"boarding pass", 3.5), (r"booking (reference|confirmation)", 3),
        (r"itinerary", 2), (r"reservation", 1.5), (r"policy (number|holder)", 2.5),
        (r"insurance", 1.5), (r"prescription", 3), (r"\bnhs number\b", 3), (r"patient", 2),
        (r"tenancy agreement", 3), (r"landlord", 2), (r"date of birth", 1.2),
        (r"check-?in", 1), (r"gate closes", 3),
    ],
}  # fmt: skip
_COMPILED = {
    cat: [(re.compile(rf"\b{p}\b", re.IGNORECASE), w) for p, w in phrases]
    for cat, phrases in PROFILES.items()
}

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".rtf", ".tex"}
OOXML_TEXT = {".docx": "word/document.xml", ".pptx": "ppt/slides/", ".xlsx": "xl/sharedStrings.xml"}

# Common screen resolutions (landscape; portrait is checked too).
SCREEN_SIZES = {
    (1280, 720), (1280, 800), (1366, 768), (1440, 900), (1536, 864), (1600, 900),
    (1680, 1050), (1920, 1080), (1920, 1200), (2048, 1152), (2160, 1440), (2256, 1504),
    (2560, 1080), (2560, 1440), (2560, 1600), (2736, 1824), (2880, 1800), (2880, 1920),
    (3000, 2000), (3024, 1964), (3440, 1440), (3456, 2234), (3840, 2160), (5120, 2880),
    (2532, 1170), (2556, 1179), (2778, 1284), (2796, 1290), (2400, 1080), (2340, 1080),
    (1920, 1080), (2208, 1242), (1334, 750), (2436, 1125), (2688, 1242),
}  # fmt: skip
SCREENSHOT_SOFTWARE = re.compile(
    r"screenshot|snipping|snip & sketch|greenshot|sharex|lightshot|flameshot|spectacle|"
    r"gnome-screenshot|screencapture|cleanshot|xbox game bar",
    re.IGNORECASE,
)
ARTWORK_SOFTWARE = re.compile(
    r"photoshop|illustrator|procreate|krita|gimp|affinity|clip studio|inkscape|figma|"
    r"blender|midjourney|stable diffusion|dall-?e",
    re.IGNORECASE,
)


@dataclass(slots=True)
class TextScore:
    category: str
    score: float
    terms: list[str]


def score_text(text: str) -> list[TextScore]:
    """Score ``text`` against every profile, best first."""
    results: list[TextScore] = []
    for category, phrases in _COMPILED.items():
        score = 0.0
        terms: list[str] = []
        for regex, weight in phrases:
            match = regex.search(text)
            if match:
                score += weight
                terms.append(match.group(0).lower())
        if score:
            results.append(TextScore(category, score, terms))
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def confidence_for(score: float, runner_up: float) -> float:
    """Map a profile score (and its margin over the runner-up) to 0..1."""
    margin = score - runner_up
    if score >= 7 and margin >= 3:
        return 0.93
    if score >= 5 and margin >= 2:
        return 0.86
    if score >= 3.5 and margin >= 1.5:
        return 0.76
    if score >= 2.5 and margin >= 1:
        return 0.64
    return 0.0


class ContentAnalyser:
    """Implements :class:`tidy.core.classifier.ContentLayer`."""

    def analyse(self, item: FileItem, current: Classification) -> Classification | None:
        if item.is_folder or item.size == 0:
            return None
        ext = item.extension
        info = extension_info(ext)
        family = info[2] if info else None
        if ext == ".ts":
            return self._transport_stream(item)
        if ext in {".zip", ".jar", ".whl"}:
            return self._zip(item)
        if family == IMG and ext in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"}:
            return self._image(item)
        if family == DOC or ext in TEXT_EXTENSIONS:
            text = extract_text(item.path, item.size)
            if text:
                return self._from_text(text, ext)
        return None

    # -- documents ------------------------------------------------------- #
    @staticmethod
    def _from_text(text: str, ext: str) -> Classification | None:
        scores = score_text(text)
        if not scores:
            return None
        best = scores[0]
        runner_up = scores[1].score if len(scores) > 1 else 0.0
        confidence = confidence_for(best.score, runner_up)
        if not confidence:
            return None
        shown = ", ".join(f"“{t}”" for t in best.terms[:4])
        return Classification(
            best.category,
            confidence,
            Source.CONTENT,
            [f"the {ext.lstrip('.').upper()}'s text mentions {shown}"],
        )

    # -- archives -------------------------------------------------------- #
    @staticmethod
    def _zip(item: FileItem) -> Classification | None:
        try:
            with zipfile.ZipFile(item.path) as zf:
                names = [i.filename for i in zf.infolist()[:MAX_ZIP_ENTRIES] if not i.is_dir()]
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
            return None
        if not names:
            return None
        lower = [n.lower() for n in names]
        basenames = {n.rsplit("/", 1)[-1] for n in lower}
        markers = {
            "pyproject.toml": "python",
            "setup.py": "python",
            "requirements.txt": "python",
            "package.json": "web",
            "cargo.toml": "rust",
            "go.mod": "go",
            "pom.xml": "java",
        }
        for marker, kind in markers.items():
            if marker in basenames:
                return Classification(
                    project_category(kind),
                    0.86,
                    Source.CONTENT,
                    [f"archive contains a {kind} project ({marker})"],
                )
        exts = Counter(Path(n).suffix for n in lower)
        code = Counter()
        for ext, count in exts.items():
            if ext in CODE_EXTENSIONS:
                code[CODE_EXTENSIONS[ext]] += count
        total = sum(exts.values())
        if code:
            lang, count = code.most_common(1)[0]
            if count >= 2 and sum(code.values()) >= 0.4 * total:
                return Classification(
                    project_category(lang),
                    0.8,
                    Source.CONTENT,
                    [f"archive mostly contains {lang} source files ({count} of {total})"],
                )
        installers = sum(exts[e] for e in (".exe", ".msi", ".msix", ".inf", ".sys", ".dll"))
        if (
            any(
                b.startswith(("setup", "install")) and b.endswith((".exe", ".msi"))
                for b in basenames
            )
            or installers >= 0.5 * total
        ):
            return Classification(
                "software.installers",
                0.75,
                Source.CONTENT,
                ["archive contains an installer or driver"],
            )
        return None

    # -- images ---------------------------------------------------------- #
    @staticmethod
    def _image(item: FileItem) -> Classification | None:
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - Pillow is a hard dependency
            return None
        try:
            with Image.open(item.path) as img:
                width, height = img.size
                exif = img.getexif()
                info = dict(img.info)
        except Exception:  # corrupt/unsupported images are common in Downloads
            return None
        make = str(exif.get(0x010F, "") or "").strip("\x00 ")
        model = str(exif.get(0x0110, "") or "").strip("\x00 ")
        software = " ".join(
            str(v)
            for v in (exif.get(0x0131, ""), info.get("Software", ""), info.get("software", ""))
        )
        comment = str(exif.get(0x9286, "") or info.get("Comment", "") or "")
        if SCREENSHOT_SOFTWARE.search(software) or "screenshot" in comment.lower():
            return Classification(
                "images.screenshots",
                0.93,
                Source.CONTENT,
                ["image metadata says it is a screenshot"],
            )
        if make or model:
            camera = " ".join(dict.fromkeys(f"{make} {model}".split()))
            return Classification(
                "images.photos", 0.93, Source.CONTENT, [f"taken with a camera ({camera})"]
            )
        if ARTWORK_SOFTWARE.search(software):
            return Classification(
                "images.artwork",
                0.85,
                Source.CONTENT,
                [f"created in {ARTWORK_SOFTWARE.search(software).group(0)}"],
            )
        if item.extension == ".png" and (
            (width, height) in SCREEN_SIZES or (height, width) in SCREEN_SIZES
        ):
            return Classification(
                "images.screenshots",
                0.78,
                Source.CONTENT,
                [f"PNG at a screen resolution ({width}×{height}) with no camera data"],
            )
        return None

    @staticmethod
    def _transport_stream(item: FileItem) -> Classification | None:
        """``.ts`` is TypeScript *or* MPEG transport stream video."""
        try:
            with open(item.path, "rb") as fh:
                head = fh.read(4096)
        except OSError:
            return None
        if b"\x00" in head or (len(head) > 376 and head[0] == 0x47 and head[188] == 0x47):
            return Classification(
                "media.video",
                0.88,
                Source.CONTENT,
                ["binary MPEG transport stream, not TypeScript"],
            )
        return None


# --------------------------------------------------------------------------- #
# Text extraction (also used to build opt-in AI snippets)
# --------------------------------------------------------------------------- #


def extract_text(path: Path, size: int | None = None, limit: int = MAX_TEXT_CHARS) -> str:
    """Best-effort, bounded plain-text extraction. Returns ``""`` on any failure."""
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size if size is None else size
        if size > MAX_PARSE_BYTES:
            return ""
        if ext == ".pdf":
            return _pdf_text(path, limit)
        if ext == ".docx":
            return _docx_text(path, limit)
        if ext in OOXML_TEXT:
            return _ooxml_text(path, OOXML_TEXT[ext], limit)
        if ext in TEXT_EXTENSIONS:
            with open(path, "rb") as fh:
                raw = fh.read(limit * 2)
            text = raw.decode("utf-8", errors="ignore")
            if ext == ".rtf":
                text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text)
            return text[:limit]
    except Exception:  # parsers face arbitrary, possibly hostile, input
        log.debug("Text extraction failed", extra={"ctx": {"path": str(path)}}, exc_info=True)
    return ""


def _pdf_text(path: Path, limit: int) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            return ""
        meta = doc.metadata or {}
        parts = [meta.get("title") or "", meta.get("subject") or "", meta.get("keywords") or ""]
        for index in range(min(PDF_PAGES, doc.page_count)):
            parts.append(doc[index].get_text("text"))
            if sum(map(len, parts)) >= limit:
                break
    return "\n".join(parts)[:limit]


def _docx_text(path: Path, limit: int) -> str:
    import docx

    document = docx.Document(str(path))
    props = document.core_properties
    parts = [props.title or "", props.subject or "", props.keywords or ""]
    total = 0
    for para in document.paragraphs:
        parts.append(para.text)
        total += len(para.text)
        if total >= limit:
            break
    return "\n".join(parts)[:limit]


_XML_TEXT = re.compile(r"<(?:a:|w:)?t(?:\s[^>]*)?>([^<]*)</(?:a:|w:)?t>")


def _ooxml_text(path: Path, member: str, limit: int) -> str:
    out: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(n for n in zf.namelist() if n.startswith(member) and n.endswith(".xml"))
        for name in names[:30]:
            info = zf.getinfo(name)
            if info.file_size > 5 * 1024 * 1024:
                continue
            xml = zf.read(name).decode("utf-8", errors="ignore")
            out.extend(_XML_TEXT.findall(xml))
            if sum(map(len, out)) >= limit:
                break
    return " ".join(out)[:limit]
