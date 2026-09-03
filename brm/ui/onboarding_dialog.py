"""Онбординг первого запуска (раздел 4.1 спеки, п.3): сразу просит путь к Blender.

Наследует ``SettingsDialog`` целиком — то же поле, та же валидация, тот же
OK, заблокированный до валидного пути — и добавляет сверху приветствие.
Бизнес-логики здесь нет, только один дополнительный QLabel.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from brm.core.storage import AppSettings
from brm.ui.settings_dialog import SettingsDialog
from brm.ui.theme import set_role

WELCOME_TEXT = (
    "Welcome to BRM — Blender Render Manager.\n"
    "Point it at your blender.exe to get started; everything else here can be changed later in Settings."
)


class OnboardingDialog(SettingsDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self.setWindowTitle("Welcome to BRM")

        welcome = QLabel(WELCOME_TEXT, self)
        welcome.setWordWrap(True)
        set_role(welcome, "muted")
        layout = self.layout()
        layout.insertWidget(0, welcome)
