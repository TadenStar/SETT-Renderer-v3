"""Панель «Project». Заглушка до M1: выбор .blend, сцены, диапазона кадров."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class ProjectPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Project", parent)
        label = QLabel(".blend file, scene, camera and frame range selection arrive in M1.", self)
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
