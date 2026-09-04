"""Окно с настройками устойчивости рендера (пропуск готовых кадров, пачки).

Отдельное окно, а не строка на главном экране: по отзыву с реальной задачи
эти поля в работе не трогают, а место занимали. Виджеты принадлежат панели
проекта — здесь только рамка вокруг них, чтобы состояние осталось в одном месте.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from brm.ui.theme import set_role

HINT = (
    "Skipping frames already on disk lets an interrupted render continue where it stopped. "
    "Chunks restart Blender every N frames so a leak or a crash costs one chunk, not the whole job."
)


class SafetyDialog(QDialog):
    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Safety")
        self.content = content

        hint = QLabel(HINT, self)
        hint.setWordWrap(True)
        set_role(hint, "muted")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(content)
        layout.addWidget(hint)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.resize(420, 240)
