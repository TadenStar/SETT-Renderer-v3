"""Подпись, которая сокращает свой текст вместо того, чтобы растягивать окно.

Пути к кадрам и строки лога длиннее любой разумной колонки. Обычный QLabel в
такой ситуации либо расширяет карточку, либо обрезает хвост без предупреждения;
здесь текст сокращается многоточием, а целиком остаётся в подсказке.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

# Минимум, ниже которого подпись не сжимает окно: без него длинный путь
# всё равно задаёт ширину карточки.
MIN_WIDTH = 80


class ElidedLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
    ) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 — имя из Qt
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — имя из Qt
        hint = super().minimumSizeHint()
        return QSize(MIN_WIDTH, hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — имя из Qt
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        width = max(self.width() - 2, MIN_WIDTH)
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, width))
