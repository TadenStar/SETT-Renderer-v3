"""Панель «Настройки рендера». Заглушка: пресеты в M4, форма из capabilities в M7."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class SettingsForm(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Настройки рендера", parent)
        label = QLabel(
            "Пресеты Draft / Balanced / Final появятся в M4, "
            "полная форма из capabilities — в M7.",
            self,
        )
        label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
