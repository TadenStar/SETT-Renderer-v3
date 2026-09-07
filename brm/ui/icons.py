"""Иконки интерфейса, нарисованные кодом.

Файлов ресурсов и шрифта иконок в проекте нет, а заводить новую зависимость
ради шести глифов незачем (правило «не добавлять зависимости без согласования»).
Каждая иконка рисуется QPainter'ом на пиксмапе в 64 px и уменьшается Qt под
нужный размер — так она остаётся чёткой на любом DPI, а цвет приходит от темы
или от кнопки, на которой лежит.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# Рисуем в этой сетке, Qt масштабирует вниз. Больше не нужно: иконки мелкие.
CANVAS = 64.0


def _pixmap(color: QColor) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(int(CANVAS), int(CANVAS))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    return pixmap, painter


def _stroke(painter: QPainter, color: QColor, width: float = 5.0) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _play(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(20.0, 14.0)
    path.lineTo(50.0, 32.0)
    path.lineTo(20.0, 50.0)
    path.closeSubpath()
    painter.fillPath(path, color)


def _pause(painter: QPainter, color: QColor) -> None:
    for left in (19.0, 34.0):
        painter.fillRect(QRectF(left, 15.0, 11.0, 34.0), color)


def _stop(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(17.0, 17.0, 30.0, 30.0), 4.0, 4.0)
    painter.fillPath(path, color)


def _cog(painter: QPainter, color: QColor) -> None:
    """Шестерёнка: восемь зубцов по кругу, кольцо и дырка посередине."""
    center = QPointF(32.0, 32.0)
    teeth = QPainterPath()
    for index in range(8):
        painter.save()
        painter.translate(center)
        painter.rotate(index * 45.0)
        teeth.addRoundedRect(QRectF(-4.5, -30.0, 9.0, 13.0), 2.0, 2.0)
        painter.fillPath(teeth, color)
        teeth.clear()
        painter.restore()
    ring = QPainterPath()
    ring.addEllipse(center, 17.0, 17.0)
    hole = QPainterPath()
    hole.addEllipse(center, 7.5, 7.5)
    painter.fillPath(ring.subtracted(hole), color)


def _file(painter: QPainter, color: QColor) -> None:
    """Лист с загнутым уголком."""
    sheet = QPainterPath()
    sheet.moveTo(16.0, 10.0)
    sheet.lineTo(38.0, 10.0)
    sheet.lineTo(48.0, 20.0)
    sheet.lineTo(48.0, 54.0)
    sheet.lineTo(16.0, 54.0)
    sheet.closeSubpath()
    corner = QPainterPath()
    corner.moveTo(38.0, 10.0)
    corner.lineTo(48.0, 20.0)
    corner.lineTo(38.0, 20.0)
    corner.closeSubpath()
    painter.fillPath(sheet.subtracted(corner), color)


def _folder(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(10.0, 18.0)
    path.lineTo(26.0, 18.0)
    path.lineTo(31.0, 24.0)
    path.lineTo(54.0, 24.0)
    path.lineTo(54.0, 48.0)
    path.lineTo(10.0, 48.0)
    path.closeSubpath()
    painter.fillPath(path, color)


def _list(painter: QPainter, color: QColor) -> None:
    """Строки лога: три полосы разной длины."""
    _stroke(painter, color, 5.0)
    for index, right in enumerate((50.0, 44.0, 50.0)):
        y = 20.0 + index * 12.0
        painter.drawLine(QPointF(14.0, y), QPointF(right, y))


_GLYPHS = {
    "play": _play,
    "pause": _pause,
    "stop": _stop,
    "cog": _cog,
    "file": _file,
    "folder": _folder,
    "list": _list,
}

GLYPHS = tuple(sorted(_GLYPHS))


def glyph_icon(name: str, color: str) -> QIcon:
    """Иконка по имени глифа и цвету. Неизвестное имя → пустая иконка, не падение."""
    draw = _GLYPHS.get(name)
    if draw is None:
        return QIcon()
    pixmap, painter = _pixmap(QColor(color))
    try:
        draw(painter, QColor(color))
    finally:
        painter.end()
    return QIcon(pixmap)
