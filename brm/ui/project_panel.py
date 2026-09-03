"""Панель «Проект». Заглушка до M1: выбор .blend, сцены, диапазона кадров."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class ProjectPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Проект", parent)
        label = QLabel("Выбор .blend, сцены, камеры и диапазона кадров появится в M1.", self)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
