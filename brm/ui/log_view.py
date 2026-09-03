"""Панель «Log». Заглушка до M2: сырой лог Blender, фильтры и команда запуска."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class LogView(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        label = QLabel("Live Blender log and the Command panel arrive in M2.", self)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
