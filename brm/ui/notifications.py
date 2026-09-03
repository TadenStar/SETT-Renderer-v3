"""Системные уведомления Windows (раздел 4.9 спеки) через иконку в трее.

Через Qt, без новых зависимостей. Если трей недоступен, уведомления молча
пропускаются: это не повод ронять приложение.
"""
from __future__ import annotations

import logging

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon, QWidget

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 8000


class Notifier:
    """Обёртка над QSystemTrayIcon: одна иконка на приложение."""

    def __init__(self, parent: QWidget | None = None, *, app_name: str = "BRM") -> None:
        self._app_name = app_name
        self._tray: QSystemTrayIcon | None = None
        self.enabled = True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("System tray is not available, notifications are disabled")
            return
        app = QApplication.instance()
        icon = QIcon()
        if app is not None:
            icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray = QSystemTrayIcon(icon, parent)
        self._tray.setToolTip(app_name)
        self._tray.show()

    @property
    def available(self) -> bool:
        return self._tray is not None

    def notify(self, title: str, message: str, *, success: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
        """Показывает уведомление. False — трея нет или уведомления выключены."""
        if self._tray is None or not self.enabled:
            return False
        kind = QSystemTrayIcon.MessageIcon.Information if success else QSystemTrayIcon.MessageIcon.Warning
        self._tray.showMessage(title, message, kind, timeout_ms)
        return True

    def hide(self) -> None:
        if self._tray is not None:
            self._tray.hide()
