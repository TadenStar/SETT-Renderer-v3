"""Окно «Detailed Settings»: всё, что раньше занимало левую колонку целиком.

Устройство, подстройка под машину, режим отображения и сами значения пресета.
На главном экране остались список пресетов, строка-сводка и кнопка сюда — так
попросил Павел после теста на реальной задаче. Виджеты принадлежат панели
настроек, здесь только рамка вокруг них.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class DetailsWindow(QDialog):
    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detailed Settings")
        self.resize(560, 640)
        self.content = content
        content.show()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(content, 1)
        layout.addWidget(buttons)
