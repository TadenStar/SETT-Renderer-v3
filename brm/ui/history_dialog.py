"""Диалог «Render History» (раздел 4.9 спеки), открывается из меню View."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from brm.ui.history_view import HistoryView


class HistoryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render History")
        self.resize(920, 560)

        self.view = HistoryView(self)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(buttons)
