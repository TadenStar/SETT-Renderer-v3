"""Панель «Log» (раздел 4.4 спеки): живой лог с фильтрами, панель «Command» с Copy.

Только отображение: классификацию строк делает core.log_parser.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from brm.core.log_parser import is_brm_line, is_error_line
from brm.ui.theme import set_role

FILTER_ALL = "all"
FILTER_BRM = "brm"
FILTER_ERRORS = "errors"
# Больше строк держать в памяти нет смысла: полный лог лежит в файле рядом с кадрами.
MAX_LINES = 50_000


class LogView(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        self._lines: list[str] = []

        self.filter_combo = QComboBox(self)
        self.filter_combo.addItem("All", FILTER_ALL)
        self.filter_combo.addItem("[BRM] only", FILTER_BRM)
        self.filter_combo.addItem("Errors", FILTER_ERRORS)
        self.filter_combo.currentIndexChanged.connect(self._rebuild)
        self.status_label = QLabel("Idle", self)
        set_role(self.status_label, "muted")
        self.copy_button = QPushButton("Copy command", self)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_command)

        top = QHBoxLayout()
        top.addWidget(self.filter_combo)
        top.addWidget(self.status_label, 1)
        top.addWidget(self.copy_button)

        self.command_edit = QLineEdit(self)
        self.command_edit.setObjectName("commandLine")
        self.command_edit.setReadOnly(True)
        self.command_edit.setPlaceholderText("The Blender command line appears here when a render starts")

        self.text = QPlainTextEdit(self)
        self.text.setObjectName("logText")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.command_edit)
        layout.addWidget(self.text, 1)

    # --- публичное API ---------------------------------------------------------

    def clear(self) -> None:
        self._lines.clear()
        self.text.clear()

    def set_command(self, text: str) -> None:
        self.command_edit.setText(text)
        self.command_edit.setCursorPosition(0)
        self.copy_button.setEnabled(bool(text))

    def command(self) -> str:
        return self.command_edit.text()

    def append_line(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]
        if self._accepts(line):
            self.text.appendPlainText(line)

    def lines(self) -> list[str]:
        return list(self._lines)

    def set_status(self, text: str, role: str = "muted") -> None:
        self.status_label.setText(text)
        set_role(self.status_label, role)

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self.command_edit.text())
        self.set_status("Command copied", "ok")

    # --- фильтр ------------------------------------------------------------------

    def _accepts(self, line: str) -> bool:
        mode = self.filter_combo.currentData()
        if mode == FILTER_BRM:
            return is_brm_line(line)
        if mode == FILTER_ERRORS:
            return is_error_line(line)
        return True

    def _rebuild(self) -> None:
        self.text.setPlainText("\n".join(line for line in self._lines if self._accepts(line)))
