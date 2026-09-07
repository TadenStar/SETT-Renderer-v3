"""Карточка «Log» на главном экране: состояние, последняя строка, кнопка.

Полный лог с фильтрами и командой запуска занимал половину правой колонки, а
нужен рывками: посмотреть, почему упало, или скопировать команду. Он переехал
в отдельное немодальное окно (``ui/log_window.py``) и продолжает получать живые
строки, пока рендер идёт. Здесь — только витрина и пересылка вызовов в него,
чтобы у лога осталось одно место хранения.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from brm.ui.elided_label import ElidedLabel
from brm.ui.log_view import LogView
from brm.ui.theme import set_role


class LogCard(QGroupBox):
    full_log_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        # Полная панель живёт здесь, а показывается в окне: владелец один,
        # поэтому строки не расходятся между двумя копиями.
        self.view = LogView()
        self.view.hide()

        self.status_label = ElidedLabel("Idle", self)
        set_role(self.status_label, "muted")
        self.last_label = ElidedLabel("", self, mode=Qt.TextElideMode.ElideLeft)
        self.last_label.setObjectName("logText")
        set_role(self.last_label, "muted")

        self.full_button = QPushButton("Show Full Log", self)
        self.full_button.clicked.connect(self.full_log_requested)

        top = QHBoxLayout()
        top.addWidget(self.status_label, 1)
        top.addWidget(self.full_button, 0)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addWidget(self.last_label)

    # --- пересылка в полную панель -------------------------------------------------

    def clear(self) -> None:
        self.view.clear()
        self.last_label.setText("")

    def append_line(self, line: str) -> None:
        self.view.append_line(line)
        self.last_label.setText(line.strip())

    def lines(self) -> list[str]:
        return self.view.lines()

    def set_command(self, text: str) -> None:
        self.view.set_command(text)

    def command(self) -> str:
        return self.view.command()

    def status_text(self) -> str:
        """Полный текст состояния: подпись показывает его сокращённым."""
        return self.status_label.full_text()

    def set_status(self, text: str, role: str = "muted") -> None:
        self.view.set_status(text, role)
        self.status_label.setText(text)
        set_role(self.status_label, role)

    def copy_command(self) -> None:
        self.view.copy_command()
        self.set_status(self.view.status_label.text(), "ok")
