"""Settings: folders, classification, categories, learned rules, AI and theme."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tidy.core.categories import OTHER, REVIEW, CategoryTree
from tidy.ui.context import AppContext
from tidy.ui.widgets import Page, muted
from tidy.utils.paths import UnsafePathError, default_downloads_dir, parse_relative_folder


class SettingsPage(Page):
    theme_changed = Signal(str)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Settings", "Changes apply to the next scan.")
        self.ctx = ctx
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        self.header_actions.addWidget(save)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        self.form = QVBoxLayout(inner)
        self.form.setSpacing(16)
        self.form.setContentsMargins(0, 0, 8, 0)
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)

        self._build_folders()
        self._build_classification()
        self._build_categories()
        self._build_rules()
        self.ai_group = QGroupBox("AI classification (optional)")
        self.ai_layout = QVBoxLayout(self.ai_group)
        self.ai_layout.addWidget(muted("AI support is not installed in this build."))
        self.form.addWidget(self.ai_group)
        self._build_appearance()
        self.form.addStretch(1)

        ctx.settings_changed.connect(self.load)
        ctx.history_changed.connect(self._load_rules)
        self.load()

    # ------------------------------------------------------------------ #
    def _build_folders(self) -> None:
        box = QGroupBox("Folders and scanning")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(str(default_downloads_dir()))
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        form.addRow("Default folder", row)
        self.include_subdirs = QCheckBox("Look inside sub-folders and organise their files")
        self.include_subdirs.setToolTip(
            "Off: each sub-folder is treated as one unit and moved whole.\n"
            "On: files inside ordinary sub-folders are organised individually. "
            "Projects are always kept whole."
        )
        form.addRow("", self.include_subdirs)
        self.include_hidden = QCheckBox("Include hidden files")
        form.addRow("", self.include_hidden)
        self.analyse_contents = QCheckBox("Read file contents to improve classification")
        self.analyse_contents.setToolTip(
            "PDF, Word and text files, ZIP listings and image "
            "metadata are read locally. Nothing leaves your PC."
        )
        form.addRow("", self.analyse_contents)
        self.form.addWidget(box)

    def _build_classification(self) -> None:
        box = QGroupBox("Classification")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(20, 95)
        self.threshold.setSingleStep(5)
        self.threshold_label = QLabel()
        self.threshold.valueChanged.connect(lambda v: self.threshold_label.setText(f"{v}%"))
        row.addWidget(self.threshold, 1)
        row.addWidget(self.threshold_label)
        form.addRow("Confidence threshold", row)
        form.addRow(
            "",
            muted(
                "Items Tidy is less sure about than this go to the Review folder "
                "instead of a category."
            ),
        )
        self.form.addWidget(box)

    def _build_categories(self) -> None:
        box = QGroupBox("Categories and folders")
        layout = QVBoxLayout(box)
        layout.addWidget(
            muted(
                "Rename where each category goes (relative to the organised "
                "folder) or switch categories off. Disabled categories fall back "
                "to their parent, then Other."
            )
        )
        self.cat_table = QTableWidget(0, 3)
        self.cat_table.setHorizontalHeaderLabels(["Category", "Folder", "Enabled"])
        self.cat_table.verticalHeader().setVisible(False)
        self.cat_table.setShowGrid(False)
        self.cat_table.setAlternatingRowColors(True)
        self.cat_table.setMinimumHeight(330)
        header = self.cat_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.cat_table)
        row = QHBoxLayout()
        add = QPushButton("Add category…")
        add.clicked.connect(self._add_category)
        row.addWidget(add)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset_categories)
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)
        self.form.addWidget(box)

    def _build_rules(self) -> None:
        box = QGroupBox("Learned rules")
        layout = QVBoxLayout(box)
        layout.addWidget(
            muted(
                "Created from category changes you approved. Matching files are "
                "filed the same way automatically."
            )
        )
        self.rules_table = QTableWidget(0, 3)
        self.rules_table.setHorizontalHeaderLabels(["Filename pattern", "Category", "Used"])
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setShowGrid(False)
        self.rules_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rules_table.setMinimumHeight(140)
        self.rules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.rules_table)
        remove = QPushButton("Remove selected rule")
        remove.clicked.connect(self._remove_rule)
        row = QHBoxLayout()
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        self.form.addWidget(box)

    def _build_appearance(self) -> None:
        box = QGroupBox("Appearance")
        form = QFormLayout(box)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        form.addRow("Theme", self.theme)
        self.form.addWidget(box)

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        s = self.ctx.settings
        self._custom = copy.deepcopy(s.custom_categories)
        self.folder_edit.setText(s.downloads_dir or "")
        self.include_subdirs.setChecked(s.include_subdirectories)
        self.include_hidden.setChecked(s.include_hidden)
        self.analyse_contents.setChecked(s.analyse_contents)
        self.threshold.setValue(round(s.confidence_threshold * 100))
        self.threshold_label.setText(f"{self.threshold.value()}%")
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        self._load_categories(self.ctx.categories)
        self._load_rules()
        if hasattr(self, "ai_panel"):
            self.ai_panel.load(s)

    def _load_categories(self, tree: CategoryTree) -> None:
        cats = tree.all()
        self.cat_table.setRowCount(len(cats))
        for row, cat in enumerate(cats):
            name = QTableWidgetItem(cat.label)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name.setData(Qt.ItemDataRole.UserRole, cat.key)
            name.setToolTip(f"{cat.key}\n{cat.description}")
            self.cat_table.setItem(row, 0, name)
            self.cat_table.setItem(row, 1, QTableWidgetItem(cat.folder))
            enabled = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled
            if cat.key not in (REVIEW, OTHER):
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            enabled.setFlags(flags)
            enabled.setCheckState(Qt.CheckState.Checked if cat.enabled else Qt.CheckState.Unchecked)
            self.cat_table.setItem(row, 2, enabled)

    def _load_rules(self) -> None:
        rules = self.ctx.learned.all()
        self.rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            pattern = QTableWidgetItem(rule.pattern)
            pattern.setData(Qt.ItemDataRole.UserRole, rule.id)
            self.rules_table.setItem(row, 0, pattern)
            label = (
                self.ctx.categories.get(rule.category).label
                if rule.category in self.ctx.categories
                else rule.category
            )
            self.rules_table.setItem(row, 1, QTableWidgetItem(label))
            self.rules_table.setItem(row, 2, QTableWidgetItem(str(rule.hits)))

    def _browse(self) -> None:
        start = self.folder_edit.text() or str(default_downloads_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Default folder", start)
        if chosen:
            self.folder_edit.setText(chosen)

    def _add_category(self) -> None:
        folder, ok = QInputDialog.getText(
            self, "Add category", "Folder (e.g. Documents/Work or Hobbies/3D Prints):"
        )
        if not ok or not folder.strip():
            return
        try:
            parts = parse_relative_folder(folder)
        except UnsafePathError as exc:
            QMessageBox.warning(self, "Invalid folder", str(exc))
            return
        key = "custom." + "_".join(p.lower().replace(" ", "_") for p in parts.parts)
        if key in self.ctx.categories or any(c["key"] == key for c in self._custom):
            QMessageBox.information(self, "Already exists", "That category already exists.")
            return
        self._custom.append({"key": key, "folder": parts.as_posix(), "label": parts.parts[-1]})
        tree = CategoryTree(self._collect_overrides(), self._custom)
        self._load_categories(tree)

    def _reset_categories(self) -> None:
        self._custom = []
        self._load_categories(CategoryTree())

    def _collect_overrides(self) -> dict[str, dict]:
        overrides: dict[str, dict] = {}
        defaults = CategoryTree(None, self._custom)
        for row in range(self.cat_table.rowCount()):
            key = self.cat_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            folder = self.cat_table.item(row, 1).text().strip().replace("\\", "/")
            enabled = self.cat_table.item(row, 2).checkState() == Qt.CheckState.Checked
            if key not in defaults:
                continue
            default = defaults.get(key)
            change: dict[str, object] = {}
            if folder != default.folder:
                change["folder"] = folder
            if not enabled:
                change["enabled"] = False
            if change:
                overrides[key] = change
        return overrides

    def _remove_rule(self) -> None:
        rows = self.rules_table.selectionModel().selectedRows()
        if not rows:
            return
        rule_id = self.rules_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self.ctx.learned.remove(int(rule_id))
        self._load_rules()

    def _save(self) -> None:
        folder = self.folder_edit.text().strip()
        if folder and not Path(folder).is_dir():
            QMessageBox.warning(self, "Folder not found", f"{folder} does not exist.")
            return
        overrides = self._collect_overrides()
        for key, change in overrides.items():
            if "folder" in change:
                try:
                    if not parse_relative_folder(str(change["folder"])).parts:
                        raise UnsafePathError("Folder cannot be empty")
                except UnsafePathError as exc:
                    QMessageBox.warning(self, "Invalid folder", f"{key}: {exc}")
                    return
        s = copy.deepcopy(self.ctx.settings)
        s.downloads_dir = folder or None
        s.include_subdirectories = self.include_subdirs.isChecked()
        s.include_hidden = self.include_hidden.isChecked()
        s.analyse_contents = self.analyse_contents.isChecked()
        s.confidence_threshold = self.threshold.value() / 100
        s.category_overrides = overrides
        s.custom_categories = self._custom
        old_theme = s.theme
        s.theme = self.theme.currentData()
        if hasattr(self, "ai_panel") and not self.ai_panel.apply(s, self):
            return
        self.ctx.save_settings(s)
        if s.theme != old_theme:
            self.theme_changed.emit(s.theme)
        QMessageBox.information(self, "Saved", "Settings saved. They apply to the next scan.")
