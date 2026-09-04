"""Окно сборки видео из секвенции, открывается из меню View.

На главном экране панель занимала треть левой колонки и зажимала настройки
рендера, ради которых окно и открывают. Сама ``VideoPanel`` принадлежит
главному окну: здесь только рамка вокруг неё.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class VideoDialog(QDialog):
    def __init__(self, panel: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Assemble Video")
        self.resize(660, 380)
        self.panel = panel

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(panel, 1)
        layout.addWidget(buttons)
