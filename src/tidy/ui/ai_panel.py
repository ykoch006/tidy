"""Settings panel for optional AI classification, with explicit consent."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tidy.ai.anthropic_classifier import AVAILABLE_MODELS, describe_transmission
from tidy.settings import AppSettings
from tidy.ui.widgets import Banner, muted


class AIPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            muted(
                "Optionally ask Claude about files Tidy is unsure of. Off by default; nothing is "
                "sent until you enable it and choose what may be shared."
            )
        )
        self.enabled = QCheckBox("Use Claude for low-confidence files")
        layout.addWidget(self.enabled)

        form = QFormLayout()
        self.model = QComboBox()
        for model_id, label in AVAILABLE_MODELS:
            self.model.addItem(label, model_id)
        form.addRow("Model", self.model)
        self.consent_names = QCheckBox("Send file names (never folders or full paths)")
        form.addRow("Allow", self.consent_names)
        self.consent_content = QCheckBox("Send short text excerpts from documents")
        form.addRow("", self.consent_content)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Leave empty to use ANTHROPIC_API_KEY or `ant auth login`")
        form.addRow("API key", self.api_key)
        self.key_hint = muted("")
        form.addRow("", self.key_hint)
        self.limit = QSpinBox()
        self.limit.setRange(1, 1000)
        self.limit.setSuffix(" files per scan")
        form.addRow("Limit", self.limit)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "Optional filing preferences, e.g. “Anything about my car goes to Personal.”"
        )
        self.instructions.setFixedHeight(70)
        form.addRow("Your instructions", self.instructions)
        layout.addLayout(form)

        self.disclosure = Banner()
        layout.addWidget(self.disclosure)

        for box in (self.enabled, self.consent_names, self.consent_content):
            box.toggled.connect(self._refresh)
        self.limit.valueChanged.connect(self._refresh)

    def _current(self) -> AppSettings:
        s = AppSettings()
        s.ai_consent_filenames = self.consent_names.isChecked()
        s.ai_consent_content = self.consent_content.isChecked()
        s.ai_max_files_per_scan = self.limit.value()
        return s

    def _refresh(self) -> None:
        on = self.enabled.isChecked()
        for widget in (
            self.model,
            self.consent_names,
            self.consent_content,
            self.api_key,
            self.limit,
            self.instructions,
        ):
            widget.setEnabled(on)
        self.consent_content.setEnabled(on and self.consent_names.isChecked())
        if not self.consent_names.isChecked():
            self.consent_content.setChecked(False)
        text = describe_transmission(self._current()).replace("\n", "<br>")
        if on and not self.consent_names.isChecked():
            text = "<b>Allow file names to use AI.</b> Without them Claude has too little to go on."
        self.disclosure.set_text(text if on else "AI is off. Nothing is sent anywhere.")
        env = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.key_hint.setText(
            "ANTHROPIC_API_KEY is set in your environment."
            if env
            else "A key typed here is stored unencrypted in Tidy's local database."
        )

    def load(self, s: AppSettings) -> None:
        self.enabled.setChecked(s.ai_enabled)
        index = self.model.findData(s.ai_model)
        if index < 0:
            self.model.addItem(s.ai_model, s.ai_model)
            index = self.model.count() - 1
        self.model.setCurrentIndex(index)
        self.consent_names.setChecked(s.ai_consent_filenames)
        self.consent_content.setChecked(s.ai_consent_content)
        self.api_key.setText(s.ai_api_key)
        self.limit.setValue(s.ai_max_files_per_scan)
        self.instructions.setPlainText(s.ai_instructions)
        self._refresh()

    def apply(self, s: AppSettings, parent: QWidget) -> bool:
        """Copy the panel into ``s``. Returns False if the user backs out."""
        newly_enabled = self.enabled.isChecked() and not s.ai_enabled
        widened = (self.consent_content.isChecked() and not s.ai_consent_content) or (
            self.consent_names.isChecked() and not s.ai_consent_filenames
        )
        s.ai_model = self.model.currentData()
        s.ai_consent_filenames = self.consent_names.isChecked()
        s.ai_consent_content = self.consent_content.isChecked() and s.ai_consent_filenames
        s.ai_api_key = self.api_key.text().strip()
        s.ai_max_files_per_scan = self.limit.value()
        s.ai_instructions = self.instructions.toPlainText().strip()
        enable = self.enabled.isChecked() and s.ai_consent_filenames
        if enable and (newly_enabled or widened):
            answer = QMessageBox.question(
                parent,
                "Send file information to Anthropic?",
                describe_transmission(s) + "\n\nTurn on AI classification?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        s.ai_enabled = enable
        return True
