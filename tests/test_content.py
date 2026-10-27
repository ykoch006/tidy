"""Content-analysis tests using synthetic PDFs, Word files, ZIPs and images."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tidy.core.categories import CategoryTree
from tidy.core.classifier import Classifier
from tidy.core.content_analyser import ContentAnalyser, extract_text, score_text
from tidy.models import FileItem, Source

CV_TEXT = """Jane Doe — Curriculum Vitae
Personal statement: motivated graduate.
Work experience: Software Intern, Example Ltd (2024).
Education: BSc Computer Science.
Key skills: Python, SQL. References available on request."""

ASSIGNMENT_TEXT = """Module code: COMP1234  Student ID: 00000000
Coursework 2 — Algorithms. Word count: 2,000.
Submission deadline: 1 March. Learning outcomes 1-3. Bibliography follows."""

INVOICE_TEXT = """INVOICE  Invoice number: 10042
Billing address: 1 Example Street. Subtotal £100.00  VAT £20.00
Amount due: £120.00. Payment due within 30 days."""


def make_pdf(path: Path, text: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path


def make_docx(path: Path, text: str) -> Path:
    import docx

    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(str(path))
    return path


def make_image(
    path: Path,
    size=(800, 600),
    exif: dict[int, str] | None = None,
    fmt: str | None = None,
    info: dict[str, str] | None = None,
) -> Path:
    from PIL import Image, PngImagePlugin

    img = Image.new("RGB", size, (120, 80, 200))
    kwargs = {}
    if exif:
        e = Image.Exif()
        for k, v in exif.items():
            e[k] = v
        kwargs["exif"] = e.tobytes()
    if info and path.suffix == ".png":
        meta = PngImagePlugin.PngInfo()
        for k, v in info.items():
            meta.add_text(k, v)
        kwargs["pnginfo"] = meta
    img.save(path, format=fmt, **kwargs)
    return path


def item_for(path: Path) -> FileItem:
    st = path.stat()
    return FileItem(path=path, root=path.parent, size=st.st_size, mtime_ns=st.st_mtime_ns)


def classify(path: Path):
    return Classifier(CategoryTree(), content=ContentAnalyser()).classify(item_for(path))


# --------------------------------------------------------------------------- #


def test_score_text_profiles() -> None:
    assert score_text(CV_TEXT)[0].category == "documents.career"
    assert score_text(ASSIGNMENT_TEXT)[0].category == "documents.university"
    assert score_text(INVOICE_TEXT)[0].category == "documents.finance"
    assert score_text("hello world") == []


def test_cv_pdf_with_generic_name(downloads: Path) -> None:
    result = classify(make_pdf(downloads / "document (3).pdf", CV_TEXT))
    assert result.category == "documents.career"
    assert result.source is Source.CONTENT
    assert result.confidence >= 0.8
    assert "curriculum vitae" in result.explain().lower()


def test_assignment_pdf(downloads: Path) -> None:
    result = classify(make_pdf(downloads / "final.pdf", ASSIGNMENT_TEXT))
    assert result.category == "documents.university"


def test_invoice_docx(downloads: Path) -> None:
    result = classify(make_docx(downloads / "scan_0001.docx", INVOICE_TEXT))
    assert result.category == "documents.finance"


def test_plain_text(downloads: Path, make_file) -> None:
    result = classify(make_file(downloads / "notes.txt", ASSIGNMENT_TEXT))
    assert result.category == "documents.university"


def test_pptx_and_xlsx_text(downloads: Path) -> None:
    pptx = downloads / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld><a:t>Lecture 4</a:t><a:t>Module code: COMP1234 — learning outcomes"
            "</a:t><a:t>Seminar and coursework</a:t></p:sld>",
        )
    assert "Module code" in extract_text(pptx)
    assert classify(pptx).category == "documents.university"
    xlsx = downloads / "sheet.xlsx"
    with zipfile.ZipFile(xlsx, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            '<sst><si><t>Opening balance</t></si><si><t xml:space="preserve">Closing '
            "balance</t></si><si><t>Sort code</t></si><tc>ignored</tc></sst>",
        )
    text = extract_text(xlsx)
    assert "Opening balance" in text and "ignored" not in text
    assert classify(xlsx).category == "documents.finance"


def test_filename_keyword_kept_when_content_is_generic(downloads: Path) -> None:
    result = classify(make_pdf(downloads / "Jane Doe CV.pdf", "Lorem ipsum dolor sit amet."))
    assert result.category == "documents.career"
    assert result.source is Source.RULE


def test_zip_with_python_project(downloads: Path) -> None:
    path = downloads / "project-main.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("project-main/pyproject.toml", "[project]")
        zf.writestr("project-main/src/app.py", "print(1)")
    result = classify(path)
    assert result.category == "projects.python"
    assert "archive" in result.explain()


def test_zip_of_python_sources_without_markers(downloads: Path) -> None:
    path = downloads / "scripts.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(4):
            zf.writestr(f"s{i}.py", "x = 1")
        zf.writestr("readme.txt", "hi")
    assert classify(path).category == "projects.python"


def test_zip_with_installer(downloads: Path) -> None:
    path = downloads / "driver.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Setup.exe", "MZ")
        zf.writestr("readme.txt", "hi")
    assert classify(path).category == "software.installers"


def test_zip_of_photos_stays_archive(downloads: Path) -> None:
    path = downloads / "pics.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.jpg", "x")
    assert classify(path).category == "archives"


def test_corrupt_files_do_not_crash(downloads: Path, make_file) -> None:
    for name in ("broken.pdf", "broken.docx", "broken.zip", "broken.png", "broken.pptx"):
        make_file(downloads / name, b"\x00\x01garbage" * 10)
        result = classify(downloads / name)
        assert result.source is not Source.CONTENT


def test_encrypted_pdf_is_skipped(downloads: Path) -> None:
    import pymupdf

    path = downloads / "secret.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), CV_TEXT)
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    assert extract_text(path) == ""
    assert classify(path).category == "documents.general"


def test_camera_photo_from_exif(downloads: Path) -> None:
    path = make_image(downloads / "image.jpg", exif={0x010F: "Canon", 0x0110: "EOS R6"})
    result = classify(path)
    assert result.category == "images.photos"
    assert "Canon" in result.explain()


def test_screenshot_from_resolution(downloads: Path) -> None:
    result = classify(make_image(downloads / "image.png", size=(1920, 1080)))
    assert result.category == "images.screenshots"


def test_screenshot_from_png_software(downloads: Path) -> None:
    result = classify(
        make_image(downloads / "x.png", size=(640, 480), info={"Software": "Greenshot"})
    )
    assert result.category == "images.screenshots"


def test_artwork_from_software(downloads: Path) -> None:
    path = make_image(downloads / "piece.jpg", exif={0x0131: "Adobe Photoshop 25.0"})
    assert classify(path).category == "images.artwork"


def test_plain_image_unchanged(downloads: Path) -> None:
    result = classify(make_image(downloads / "pic.jpg", size=(500, 500)))
    assert result.category == "images.general"


def test_ts_video_vs_typescript(downloads: Path, make_file) -> None:
    video = bytes([0x47] + [0] * 187) * 4
    assert classify(make_file(downloads / "clip.ts", video)).category == "media.video"
    code = make_file(downloads / "app.ts", "export const x: number = 1;\n")
    assert classify(code).category == "projects.web"


@pytest.mark.slow
def test_large_pdf_only_reads_first_pages(downloads: Path) -> None:
    import pymupdf

    path = downloads / "book.pdf"
    doc = pymupdf.open()
    for i in range(200):
        doc.new_page().insert_text((72, 72), f"page {i} " + ("filler " * 50))
    doc.new_page().insert_text((72, 72), CV_TEXT)  # only on the last page
    doc.save(path)
    doc.close()
    assert "Curriculum" not in extract_text(path)
