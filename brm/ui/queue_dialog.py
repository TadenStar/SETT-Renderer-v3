"""Окно очереди задач, открывается из меню View.

На главном экране очередь занимала треть правой колонки, а в работе с одним
проектом не нужна вовсе — по отзыву с реальной задачи она уехала сюда.
Сам ``QueueView`` принадлежит главному окну: здесь только рамка вокруг него.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class QueueDialog(QDialog):
    def __init__(self, view: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render Queue")
        self.resize(860, 420)
        self.view = view

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(view, 1)
        layout.addWidget(buttons)
