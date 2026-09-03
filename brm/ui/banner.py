"""Оранжевая плашка-предупреждение вверху главного окна."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


class WarningBanner(QFrame):
    """Кликабельная плашка «Blender не настроен → Открыть настройки»."""

    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("warningBanner")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "#warningBanner { background-color: #E8891C; border-bottom: 1px solid #B86A0E; }"
            "#warningBanner QLabel { color: #1B1B1B; font-weight: 600; }"
        )
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._button = QPushButton("Открыть настройки", self)
        self._button.clicked.connect(self.action_clicked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._button, 0)

    def set_message(self, text: str) -> None:
        self._label.setText(text)

    def message(self) -> str:
        return self._label.text()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — имя из Qt
        # Клик по любой точке плашки ведёт в настройки, не только по кнопке.
        if event.button() == Qt.MouseButton.LeftButton:
            self.action_clicked.emit()
        super().mousePressEvent(event)
