"""Окно «All settings»: каждое свойство, которое отдаёт этот Blender.

Вынесено с главного экрана по отзыву с реальной задачи: сотни полей пугали
больше, чем помогали, а трогают их редко. Сама форма принадлежит панели
настроек — здесь только рамка и пояснение, откуда берутся значения.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from brm.ui.theme import set_role

HINT = (
    "While this window is in use, values come from here instead of the Simple rows. "
    "Rows left on “Preset” keep the preset value; “Don't touch” leaves the .blend as it is."
)


class ExpertWindow(QDialog):
    def __init__(self, form: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("All Settings")
        self.resize(760, 720)
        self.form = form

        hint = QLabel(HINT, self)
        hint.setWordWrap(True)
        set_role(hint, "muted")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(form, 1)
        layout.addWidget(buttons)
