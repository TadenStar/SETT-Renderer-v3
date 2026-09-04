"""График времени кадров: несколько прогонов на одних осях.

X — номер кадра слева направо без подписей, Y — секунды на кадр. Синим идёт
текущий рендер, красным прошлые (чем старше, тем темнее), золотым — эталон.
Что рисовать и в каких границах, решает ``core.frame_chart``; здесь только
цвета и рисование.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from brm.core.frame_chart import (
    ROLE_CURRENT,
    ROLE_RECENT,
    ROLE_REFERENCE,
    ChartBounds,
    ChartData,
    ChartSeries,
    chart_bounds,
)
from brm.ui.theme import current_theme, tokens_for

# Насколько бледнее каждая следующая по возрасту линия. Самая старая из пяти
# остаётся различимой, но уже не спорит за внимание с текущим рендером.
AGE_FADE = 0.16
MIN_AGE_ALPHA = 0.28


class FrameChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: list[ChartSeries] = []
        self._bounds = ChartBounds()
        self._live = False
        self._hover: list[tuple[QPointF, ChartSeries, int, float]] = []
        self.setMinimumHeight(90)
        self.setMouseTracking(True)

    # --- публичное API ---------------------------------------------------------

    def set_series(self, series: list[ChartSeries], *, live: bool = False) -> None:
        self._series = list(series)
        self._live = live
        self._bounds = chart_bounds(self._series, live=live)
        self.update()

    def series(self) -> list[ChartSeries]:
        return list(self._series)

    def bounds(self) -> ChartBounds:
        return self._bounds

    def clear(self) -> None:
        self.set_series([])

    # --- цвета ------------------------------------------------------------------

    def _tokens(self) -> dict[str, str]:
        return tokens_for(current_theme(None) or "dark")

    def color_for(self, series: ChartSeries) -> QColor:
        tokens = self._tokens()
        if series.role == ROLE_CURRENT:
            return QColor(tokens["chart_current"])
        if series.role == ROLE_REFERENCE:
            return QColor(tokens["chart_reference"])
        color = QColor(tokens["chart_recent"])
        # Чем старше прогон, тем он темнее и прозрачнее: свежие читаются первыми.
        factor = max(1.0 - AGE_FADE * series.age, MIN_AGE_ALPHA)
        color = color.darker(int(100 / factor))
        color.setAlphaF(max(factor, MIN_AGE_ALPHA))
        return color

    # --- рисование -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — имя из Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tokens = self._tokens()
        rect = self.rect().adjusted(4, 8, -4, -6)

        painter.setPen(QPen(QColor(tokens["chart_axis"]), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        self._hover = []
        if self._bounds.empty:
            return

        # Потолок чуть выше пика, иначе самая высокая точка липнет к краю.
        ceiling = self._bounds.seconds_max * 1.1 or 1.0
        span = self._bounds.frame_span

        for series in ChartData(self._series, self._bounds).visible():
            points: list[QPointF] = []
            for frame, seconds in series.points:
                if span:
                    ratio = (frame - self._bounds.frame_min) / span
                else:
                    ratio = 0.5
                if not 0.0 <= ratio <= 1.0:
                    continue  # во время рендера прошлые прогоны длиннее оси
                x = rect.left() + ratio * rect.width()
                y = rect.bottom() - (seconds / ceiling) * rect.height()
                point = QPointF(x, y)
                points.append(point)
                self._hover.append((point, series, frame, seconds))
            if not points:
                continue

            color = self.color_for(series)
            if series.role == ROLE_CURRENT and len(points) > 1:
                fill = QPainterPath()
                fill.moveTo(points[0].x(), rect.bottom())
                for point in points:
                    fill.lineTo(point)
                fill.lineTo(points[-1].x(), rect.bottom())
                shade = QColor(color)
                shade.setAlpha(36)
                painter.fillPath(fill, shade)

            width = 2.0 if series.role == ROLE_CURRENT else 1.4
            pen = QPen(color, width)
            if series.role == ROLE_REFERENCE:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            if len(points) > 1:
                painter.drawPolyline(points)
            else:
                painter.drawEllipse(points[0], 2.5, 2.5)

            if series.role == ROLE_CURRENT:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(points[-1], 3.0, 3.0)
                painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setPen(QPen(QColor(tokens["muted"]), 1))
        painter.drawText(rect.adjusted(2, -6, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                         f"{self._bounds.seconds_max:.1f} s")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — имя из Qt
        if not self._hover:
            self.setToolTip("")
            return
        position = event.position()
        point, series, frame, seconds = min(
            self._hover,
            key=lambda item: (item[0].x() - position.x()) ** 2 + (item[0].y() - position.y()) ** 2,
        )
        self.setToolTip(f"{series.label} · frame {frame}: {seconds:.2f} s")
        super().mouseMoveEvent(event)
