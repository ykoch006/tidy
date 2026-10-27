"""The main window: a sidebar for navigation and a stack of pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tidy import APP_NAME, __version__
from tidy.ui import theme
from tidy.ui.context import AppContext
from tidy.ui.models import PlanModel
from tidy.ui.pages.dashboard import DashboardPage
from tidy.ui.pages.explorer import ExplorerPage
from tidy.ui.pages.history import HistoryPage
from tidy.ui.pages.preview import PreviewPage
from tidy.ui.pages.settings import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 820)
        self.setMinimumSize(980, 620)

        self.model = PlanModel(
            planner_factory=lambda: ctx.planner,
            label_for=lambda key: ctx.categories.resolve(key).folder.replace("/", " › "),
            parent=self,
        )

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- sidebar ------------------------------------------------------ #
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 20, 14, 16)
        side.setSpacing(4)
        brand = QLabel(APP_NAME)
        brand.setObjectName("Brand")
        side.addWidget(brand)
        sub = QLabel("Safe, smart file organising")
        sub.setObjectName("BrandSub")
        side.addWidget(sub)
        side.addSpacing(18)

        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.pages: dict[str, QWidget] = {}

        self.dashboard = DashboardPage(ctx)
        self.explorer = ExplorerPage(ctx, self.model)
        self.preview = PreviewPage(ctx, self.model)
        self.history = HistoryPage(ctx)
        self.settings = SettingsPage(ctx)
        self._entries: list[tuple[str, str, QWidget]] = [
            ("dashboard", "Dashboard", self.dashboard),
            ("explorer", "File Explorer", self.explorer),
            ("preview", "Organisation Preview", self.preview),
        ]
        self._tail: list[tuple[str, str, QWidget]] = [
            ("history", "History", self.history),
            ("settings", "Settings", self.settings),
        ]
        self._side_layout = side
        self._built = False

        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.dashboard.review_requested.connect(lambda: self.show_page("preview"))
        self.settings.theme_changed.connect(self._apply_theme)
        ctx.plan_changed.connect(self._sync_model)
        ctx.scan_started.connect(lambda r: self.statusBar().showMessage(f"Scanning {r}…"))
        ctx.scan_finished.connect(
            lambda res: self.statusBar().showMessage(
                f"Scan complete — {len(res.items):,} items. Nothing has been moved.", 8000
            )
        )
        ctx.scan_failed.connect(self._scan_failed)
        ctx.scan_cancelled.connect(lambda: self.statusBar().showMessage("Scan cancelled", 5000))
        ctx.execute_finished.connect(
            lambda r: self.statusBar().showMessage(f"Moved {r.done:,} items", 8000)
        )
        QShortcut(QKeySequence("Ctrl+R"), self, activated=ctx.start_scan)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)

    def add_page(self, key: str, label: str, page: QWidget) -> None:
        """Register an extra page (used by optional features) before ``build``."""
        index = len(self._entries)
        self._entries.insert(index, (key, label, page))

    def build(self) -> None:
        if self._built:
            return
        self._built = True
        side = self._side_layout
        for key, label, page in [*self._entries, *self._tail]:
            if key == "history":
                side.addStretch(1)
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self.nav.addButton(button)
            side.addWidget(button)
            self.pages[key] = page
            page.setProperty("nav_button", button)
            self.stack.addWidget(page)
        version = QLabel(f"v{__version__}")
        version.setObjectName("BrandSub")
        side.addSpacing(8)
        side.addWidget(version)
        self.show_page("dashboard")

    def show_page(self, key: str) -> None:
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        button = page.property("nav_button")
        if button is not None:
            button.setChecked(True)

    def _sync_model(self) -> None:
        result = self.ctx.result
        self.model.set_plan(result.plan if result else None)

    def _focus_search(self) -> None:
        page = self.stack.currentWidget()
        search = getattr(page, "search", None)
        if search is not None:
            search.setFocus()
            search.selectAll()

    def _scan_failed(self, message: str) -> None:
        self.statusBar().showMessage("Scan failed", 5000)
        QMessageBox.warning(self, "Scan failed", message)

    def _apply_theme(self, name: str) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme.apply(app, name)
        self.model.layoutChanged.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.ctx.busy:
            answer = QMessageBox.question(
                self,
                "Tidy is still working",
                "Stop the current task and quit? Moves already completed are recorded and can "
                "be undone; the move in progress will be finished or rolled back safely.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.ctx.shutdown()
        event.accept()
