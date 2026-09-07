"""График времени кадров: несколько прогонов на одних осях.

X — номер кадра, Y — секунды на кадр. Синим идёт текущий рендер, красным
прошлые (чем старше, тем темнее), золотым — эталон. Что рисовать, в каких
границах и какими делениями, решает ``core.frame_chart``; здесь только цвета,
раскладка полей и рисование.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
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
    frame_ticks,
    value_ticks,
)
from brm.ui.theme import current_theme, tokens_for

# Насколько бледнее каждая следующая по возрасту линия. Самая старая из пяти
# остаётся различимой, но уже не спорит за внимание с текущим рендером.
AGE_FADE = 0.16
MIN_AGE_ALPHA = 0.28

TITLE = "Render Time per Frame"
Y_TITLE = "Time (s)"
X_TITLE = "Frame Number"

# Поля вокруг области рисования. Слева — числа секунд и повёрнутая подпись оси,
# снизу — номера кадров и подпись оси, сверху — заголовок и легенда.
MARGIN = 6
TITLE_H = 18
Y_TITLE_W = 14
Y_LABEL_W = 34
X_LABEL_H = 15
X_TITLE_H = 15
# Место справа под последнюю подпись оси кадров: без него её обрезало краем.
X_LABEL_PAD = 26
# Точки на линии при большом числе кадров сливаются в кашу и тормозят отрисовку.
MAX_DOTS = 160


class FrameChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: list[ChartSeries] = []
        self._bounds = ChartBounds()
        self._live = False
        self._hover: list[tuple[QPointF, ChartSeries, int, float]] = []
        self.setMinimumHeight(180)
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

    # --- раскладка ---------------------------------------------------------------

    def plot_rect(self) -> QRect:
        """Прямоугольник самого графика — без полей под подписи осей."""
        rect = self.rect().adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        return rect.adjusted(
            Y_TITLE_W + Y_LABEL_W,
            TITLE_H,
            -X_LABEL_PAD,
            -(X_LABEL_H + X_TITLE_H),
        )

    def _small_font(self, painter: QPainter, delta: int = -2) -> None:
        font = painter.font()
        font.setPointSize(max(font.pointSize() + delta, 6))
        painter.setFont(font)

    # --- рисование -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — имя из Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tokens = self._tokens()
        plot = self.plot_rect()
        if plot.width() < 20 or plot.height() < 20:
            return

        self._draw_titles(painter, tokens, plot)
        self._hover = []
        if self._bounds.empty:
            self._draw_axes(painter, tokens, plot, [], [], 1.0)
            painter.setPen(QPen(QColor(tokens["muted"]), 1))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "No frame times yet")
            return

        ticks_y = value_ticks(self._bounds.seconds_max)
        ceiling = ticks_y[-1] or 1.0
        ticks_x = frame_ticks(self._bounds.frame_min, self._bounds.frame_max)
        self._draw_axes(painter, tokens, plot, ticks_y, ticks_x, ceiling)
        self._draw_series(painter, plot, ceiling)
        self._draw_legend(painter, tokens, plot)

    def _draw_titles(self, painter: QPainter, tokens: dict[str, str], plot: QRect) -> None:
        painter.setPen(QPen(QColor(tokens["chart_text"]), 1))
        self._small_font(painter, -1)
        painter.drawText(QRect(plot.left(), MARGIN, plot.width(), TITLE_H),
                         Qt.AlignmentFlag.AlignCenter, TITLE)
        painter.drawText(QRect(plot.left(), plot.bottom() + X_LABEL_H, plot.width(), X_TITLE_H),
                         Qt.AlignmentFlag.AlignCenter, X_TITLE)

        # Подпись оси Y идёт вертикально: горизонтально она съедала бы ширину графика.
        painter.save()
        painter.translate(MARGIN + Y_TITLE_W - 2, plot.center().y())
        painter.rotate(-90.0)
        painter.drawText(QRectF(-plot.height() / 2, -Y_TITLE_W, plot.height(), Y_TITLE_W),
                         Qt.AlignmentFlag.AlignCenter, Y_TITLE)
        painter.restore()
        self._small_font(painter, 1)

    def _draw_axes(
        self,
        painter: QPainter,
        tokens: dict[str, str],
        plot: QRect,
        ticks_y: list[float],
        ticks_x: list[int],
        ceiling: float,
    ) -> None:
        self._small_font(painter, -2)
        grid = QPen(QColor(tokens["chart_grid"]), 1)
        text = QPen(QColor(tokens["chart_text"]), 1)

        for value in ticks_y:
            y = plot.bottom() - (value / ceiling) * plot.height()
            painter.setPen(grid)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text)
            painter.drawText(
                QRectF(plot.left() - Y_LABEL_W - 4, y - 8, Y_LABEL_W, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{value:g}",
            )

        span = max(self._bounds.frame_span, 0)
        for frame in ticks_x:
            ratio = (frame - self._bounds.frame_min) / span if span else 0.5
            x = plot.left() + ratio * plot.width()
            painter.setPen(grid)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(text)
            painter.drawText(
                QRectF(x - 30, plot.bottom() + 2, 60, X_LABEL_H),
                Qt.AlignmentFlag.AlignCenter,
                str(frame),
            )

        painter.setPen(QPen(QColor(tokens["chart_axis"]), 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.drawLine(plot.topLeft(), plot.bottomLeft())
        self._small_font(painter, 2)

    def _draw_series(self, painter: QPainter, plot: QRect, ceiling: float) -> None:
        span = self._bounds.frame_span
        for series in ChartData(self._series, self._bounds).visible():
            points: list[QPointF] = []
            for frame, seconds in series.points:
                ratio = (frame - self._bounds.frame_min) / span if span else 0.5
                if not 0.0 <= ratio <= 1.0:
                    continue  # во время рендера прошлые прогоны длиннее оси
                point = QPointF(
                    plot.left() + ratio * plot.width(),
                    plot.bottom() - (min(seconds, ceiling) / ceiling) * plot.height(),
                )
                points.append(point)
                self._hover.append((point, series, frame, seconds))
            if not points:
                continue

            color = self.color_for(series)
            if series.role == ROLE_CURRENT and len(points) > 1:
                fill = QPainterPath()
                fill.moveTo(points[0].x(), plot.bottom())
                for point in points:
                    fill.lineTo(point)
                fill.lineTo(points[-1].x(), plot.bottom())
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

            # Точки данных — как в макете, но только пока их можно различить.
            if series.role == ROLE_CURRENT and len(points) <= MAX_DOTS:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                for point in points:
                    painter.drawEllipse(point, 2.0, 2.0)
                painter.setBrush(Qt.BrushStyle.NoBrush)

            if series.role == ROLE_CURRENT:
                # Указатель на текущем кадре: где именно идёт рендер прямо сейчас.
                marker = QPen(color, 1)
                marker.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(marker)
                painter.drawLine(QPointF(points[-1].x(), plot.top()), QPointF(points[-1].x(), plot.bottom()))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(points[-1], 3.5, 3.5)
                painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_legend(self, painter: QPainter, tokens: dict[str, str], plot: QRect) -> None:
        """Что означают цвета. Показываем только те роли, которые сейчас нарисованы."""
        roles = {series.role for series in self._series if series.points}
        entries = ((ROLE_CURRENT, "current"), (ROLE_RECENT, "earlier"), (ROLE_REFERENCE, "reference"))
        shown = [(role, title) for role, title in entries if role in roles]
        if not shown:
            return
        self._small_font(painter, -2)
        metrics = painter.fontMetrics()
        x = float(plot.right())
        y = float(MARGIN + TITLE_H / 2)
        for role, title in reversed(shown):
            width = metrics.horizontalAdvance(title)
            sample = next(s for s in self._series if s.role == role and s.points)
            painter.setPen(QPen(QColor(tokens["chart_text"]), 1))
            painter.drawText(QRectF(x - width, y - 8, width, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, title)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color_for(sample))
            painter.drawEllipse(QPointF(x - width - 8, y), 3.0, 3.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x -= width + 20
        self._small_font(painter, 2)

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
