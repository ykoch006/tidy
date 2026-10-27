"""Application entry point: ``python -m tidy`` or the ``tidy`` launcher."""

from __future__ import annotations

import sys
import traceback
from types import TracebackType

from tidy import APP_NAME, __version__
from tidy.utils.logging import configure_logging, get_logger
from tidy.utils.paths import data_dir

log = get_logger(__name__)


def database_path():  # type: ignore[no-untyped-def]
    return data_dir() / "tidy.sqlite3"


def create_window(ctx):  # type: ignore[no-untyped-def]
    """Build the main window with every optional feature registered."""
    from tidy.ui.features import install_features
    from tidy.ui.main_window import MainWindow

    window = MainWindow(ctx)
    install_features(ctx, window)
    window.build()
    return window


def main(argv: list[str] | None = None) -> int:
    configure_logging(data_dir() / "logs")
    log.info("Starting", extra={"ctx": {"version": __version__}})

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from tidy.database import Database
    from tidy.ui import theme
    from tidy.ui.context import AppContext

    if sys.platform == "win32":  # own taskbar group/icon instead of python.exe's
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Tidy.FileOrganiser")

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    from PySide6.QtGui import QIcon

    icon_path = theme.ASSETS / "tidy.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    def excepthook(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(
            None,
            "Unexpected error",
            "Something went wrong. Your files are safe: every move is journaled and can be "
            "undone from History.\n\n" + "".join(traceback.format_exception_only(exc_type, exc)),
        )

    sys.excepthook = excepthook

    db = Database(database_path())
    ctx = AppContext(db)
    theme.apply(app, ctx.settings.theme)

    report = ctx.recover()
    window = create_window(ctx)
    if report.needed:
        window.history.show_recovery(report.resolved)
        window.show_page("history")
    window.show()
    code = app.exec()
    db.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
