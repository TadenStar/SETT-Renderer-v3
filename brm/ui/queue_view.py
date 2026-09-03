"""Панель «Очередь». Заглушка: список задач и последовательный прогон — после M3."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class QueueView(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Очередь", parent)
        label = QLabel("Список задач и последовательный прогон появятся после M3.", self)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
