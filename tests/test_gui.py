"""End-to-end GUI tests (offscreen Qt) against a synthetic Downloads folder."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.gui

from PySide6.QtCore import Qt  # noqa: E402

from tidy.app import create_window  # noqa: E402
from tidy.core.history import OpStatus  # noqa: E402
from tidy.ui.context import AppContext  # noqa: E402
from tidy.ui.models import Col  # noqa: E402


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Modal boxes would block an offscreen test run forever; record them instead."""
    from PySide6.QtWidgets import QMessageBox

    shown: list[tuple[str, tuple]] = []

    def fake(kind, answer):
        def _show(*args, **kwargs):
            shown.append((kind, args))
            return answer

        return staticmethod(_show)

    No = QMessageBox.StandardButton.No
    for kind, answer in (
        ("warning", None),
        ("information", None),
        ("critical", None),
        ("question", No),
    ):
        monkeypatch.setattr(QMessageBox, kind, fake(kind, answer))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    return shown


@pytest.fixture
def populated(downloads: Path, make_file) -> Path:
    make_file(downloads / "Jane Doe CV.pdf", b"%PDF-1.4 cv")
    make_file(downloads / "Screenshot 2026-02-01.png", b"png")
    make_file(downloads / "track.mp3", b"ID3")
    make_file(downloads / "Setup.exe", b"MZ")
    make_file(downloads / "odd.qqq", b"?")
    return downloads


@pytest.fixture
def window(qtbot, db):
    ctx = AppContext(db)
    win = create_window(ctx)
    qtbot.addWidget(win)
    yield win
    ctx.shutdown()


def scan(qtbot, win) -> None:
    with qtbot.waitSignal(win.ctx.scan_finished, timeout=20_000):
        win.ctx.start_scan()


def test_window_builds_with_all_pages(window) -> None:
    for key in ("dashboard", "explorer", "preview", "history", "settings"):
        assert key in window.pages
        window.show_page(key)
    assert window.ctx.root == Path(window.ctx.root)


def test_default_root_is_sandbox(window, downloads: Path) -> None:
    assert window.ctx.root == downloads


def test_scan_populates_model_and_dashboard(qtbot, window, populated: Path) -> None:
    scan(qtbot, window)
    assert window.model.rowCount() == 5
    assert window.dashboard.stat_files.value.text() == "5"
    assert window.dashboard.table.rowCount() >= 4
    # Nothing moved by scanning
    assert sorted(p.name for p in populated.iterdir()) == sorted(
        ["Jane Doe CV.pdf", "Screenshot 2026-02-01.png", "track.mp3", "Setup.exe", "odd.qqq"]
    )


def test_dry_run_blocks_execute(qtbot, window, populated: Path) -> None:
    scan(qtbot, window)
    preview = window.preview
    assert preview.dry_run.isChecked()
    assert not preview.execute_btn.isEnabled()
    preview.dry_run.setChecked(False)
    assert preview.execute_btn.isEnabled()


def test_edit_reject_execute_and_undo(qtbot, window, populated: Path) -> None:
    scan(qtbot, window)
    model = window.model
    rows = {model.move_at(r).item.name: r for r in range(model.rowCount())}

    # Reject the installer, re-file the screenshot, send the mp3 to a custom folder.
    model.setData(
        model.index(rows["Setup.exe"], Col.APPROVE),
        Qt.CheckState.Unchecked.value,
        Qt.ItemDataRole.CheckStateRole,
    )
    model.setData(model.index(rows["Screenshot 2026-02-01.png"], Col.CATEGORY), "images.artwork")
    model.setData(model.index(rows["track.mp3"], Col.DESTINATION), "Media/Podcasts")
    errors: list[str] = []
    model.error.connect(errors.append)
    assert not model.setData(model.index(rows["track.mp3"], Col.DESTINATION), "../escape")
    assert errors

    moves = model.plan.actionable()
    assert len(moves) == 4
    with qtbot.waitSignal(window.ctx.execute_finished, timeout=20_000) as blocker:
        window.ctx.execute(moves)
    result = blocker.args[0]
    assert result.status is OpStatus.COMPLETED
    assert (populated / "Setup.exe").exists()
    assert (populated / "Images/Artwork/Screenshot 2026-02-01.png").exists()
    assert (populated / "Media/Podcasts/track.mp3").exists()
    assert model.plan is None, "plan must be cleared after execution"
    assert window.preview.dry_run.isChecked(), "dry run re-enabled after execution"

    # The correction was learned.
    assert any(r.category == "images.artwork" for r in window.ctx.learned.all())

    history = window.history
    assert history.ops.rowCount() == 1
    history.ops.selectRow(0)
    assert history.undo_btn.isEnabled()
    assert history.moves.rowCount() == 4
    done: list[object] = []
    with qtbot.waitSignal(window.ctx.history_changed, timeout=20_000):
        window.ctx.undo(result.operation_id, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    assert (populated / "track.mp3").exists()
    assert not (populated / "Media").exists()


def test_settings_save_roundtrip(qtbot, window) -> None:
    page = window.settings
    page.threshold.setValue(80)
    page.include_subdirs.setChecked(True)
    # Rename the Archives folder
    for row in range(page.cat_table.rowCount()):
        if page.cat_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "archives":
            page.cat_table.item(row, 1).setText("Compressed")
    page._save()
    assert window.ctx.settings.confidence_threshold == 0.8
    assert window.ctx.settings.include_subdirectories
    assert window.ctx.categories.get("archives").folder == "Compressed"


def test_settings_rejects_unsafe_category_folder(qtbot, window, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    page = window.settings
    page.cat_table.item(0, 1).setText("..\\..\\Windows")
    page._save()
    assert warnings
    assert window.ctx.categories.get("documents.university").folder == "Documents/University"


def test_screenshot(qtbot, window, populated: Path, tmp_path: Path) -> None:
    """Render each page offscreen; guards against layout crashes."""
    window.resize(1320, 820)
    window.show()
    scan(qtbot, window)
    for key in window.pages:
        window.show_page(key)
        qtbot.wait(20)
        image = window.grab()
        assert not image.isNull()
        image.save(str(tmp_path / f"{key}.png"))
