"""Окно полного лога, открывается кнопкой «Show Full Log».

Немодальное намеренно: во время рендера по логу читают вывод денойзера и
компиляции ядер, и главное окно в это время должно оставаться живым.
Сама панель принадлежит карточке лога — здесь только рамка вокруг неё.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget


class LogWindow(QDialog):
    def __init__(self, view: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log")
        self.resize(900, 560)
        self.view = view
        view.show()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(view, 1)
        layout.addWidget(buttons)
