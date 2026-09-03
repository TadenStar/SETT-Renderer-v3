"""Sparkline времени кадров: X — номер кадра слева направо без подписей, Y — секунды."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QWidget


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[tuple[int, float]] = []
        self._points: list[QPointF] = []
        self.setMinimumHeight(56)
        self.setMouseTracking(True)

    def set_values(self, values: list[tuple[int, float]]) -> None:
        self._values = list(values)
        self.update()

    def values(self) -> list[tuple[int, float]]:
        return list(self._values)

    def clear(self) -> None:
        self.set_values([])

    # --- рисование -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — имя из Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        line_color = palette.color(QPalette.ColorRole.Highlight)
        base_color = palette.color(QPalette.ColorRole.Mid)
        rect = self.rect().adjusted(2, 4, -2, -4)

        painter.setPen(QPen(base_color, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        self._points = []
        if not self._values:
            return

        peak = max(seconds for _, seconds in self._values) or 1.0
        count = len(self._values)
        step = rect.width() / max(count - 1, 1)
        for index, (_, seconds) in enumerate(self._values):
            x = rect.left() + (index * step if count > 1 else rect.width() / 2)
            y = rect.bottom() - (seconds / (peak * 1.1)) * rect.height()
            self._points.append(QPointF(x, y))

        if count > 1:
            fill = QPainterPath()
            fill.moveTo(self._points[0].x(), rect.bottom())
            for point in self._points:
                fill.lineTo(point)
            fill.lineTo(self._points[-1].x(), rect.bottom())
            fill_color = QColor(line_color)
            fill_color.setAlpha(40)
            painter.fillPath(fill, fill_color)
            painter.setPen(QPen(line_color, 1.5))
            painter.drawPolyline(self._points)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(line_color)
        painter.drawEllipse(self._points[-1], 2.5, 2.5)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — имя из Qt
        if not self._points:
            self.setToolTip("")
            return
        x = event.position().x()
        index = min(range(len(self._points)), key=lambda i: abs(self._points[i].x() - x))
        frame, seconds = self._values[index]
        self.setToolTip(f"Frame {frame}: {seconds:.2f} s")
        super().mouseMoveEvent(event)
