"""Что показывать на графике времени кадров: набор линий и границы осей.

Идея Павла: X — номер кадра слева направо без подписей, Y — время кадра.
Синим идёт текущий рендер, красным — последние прошедшие, и чем старше, тем
темнее. Один рендер можно объявить эталоном — он рисуется золотым и не
вытесняется из набора возрастом.

Масштаб зависит от того, идёт ли рендер прямо сейчас. Во время рендера по
горизонтали видно только текущий прогон: иначе линия ползла бы по маленькому
куску оси, растянутой под длинный прошлый рендер. В покое ось охватывает всё,
что показано, — иначе линии не сравнить.

Чистые функции без Qt: цвета и рисование — в ``ui/frame_chart.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Сколько прошлых рендеров показывать. Пять — из отзыва: больше линий
# читаются как каша, меньше не даёт увидеть тенденцию.
DEFAULT_RECENT = 5

ROLE_CURRENT = "current"
ROLE_RECENT = "recent"
ROLE_REFERENCE = "reference"


@dataclass(frozen=True)
class ChartSeries:
    """Одна линия. ``age``: 0 — самый свежий из прошлых, дальше глубже в историю."""

    label: str
    points: tuple[tuple[int, float], ...]
    role: str
    age: int = 0
    entry_id: int | None = None

    @property
    def peak(self) -> float:
        return max((seconds for _, seconds in self.points), default=0.0)

    @property
    def frames(self) -> tuple[int, int] | None:
        if not self.points:
            return None
        numbers = [frame for frame, _ in self.points]
        return min(numbers), max(numbers)


@dataclass(frozen=True)
class ChartBounds:
    """Границы осей. ``empty`` — рисовать нечего, кроме базовой линии."""

    frame_min: int = 0
    frame_max: int = 0
    seconds_max: float = 0.0
    empty: bool = True

    @property
    def frame_span(self) -> int:
        return max(self.frame_max - self.frame_min, 0)


@dataclass
class ChartData:
    series: list[ChartSeries] = field(default_factory=list)
    bounds: ChartBounds = field(default_factory=ChartBounds)

    def visible(self) -> list[ChartSeries]:
        """Порядок рисования: старое внизу, текущий и эталон поверх всего."""
        order = {ROLE_RECENT: 0, ROLE_REFERENCE: 1, ROLE_CURRENT: 2}
        return sorted(self.series, key=lambda s: (order.get(s.role, 0), -s.age))


def build_series(
    current: list[tuple[int, float]] | None,
    history: list[tuple[int, str, list[tuple[int, float]]]],
    *,
    reference_id: int | None = None,
    recent_limit: int = DEFAULT_RECENT,
) -> list[ChartSeries]:
    """Линии графика из текущего рендера и истории.

    ``history`` — ``(id, подпись, точки)`` от новых к старым, как отдаёт
    ``HistoryStore.list_entries``. Эталон берётся из истории по ``reference_id``
    и не занимает место среди последних: иначе, состарившись, он бы пропал.
    """
    series: list[ChartSeries] = []
    if current:
        series.append(ChartSeries("Current render", tuple(current), ROLE_CURRENT))

    age = 0
    for entry_id, label, points in history:
        if not points:
            continue
        if reference_id is not None and entry_id == reference_id:
            series.append(ChartSeries(label, tuple(points), ROLE_REFERENCE, entry_id=entry_id))
            continue
        if age >= recent_limit:
            continue
        series.append(ChartSeries(label, tuple(points), ROLE_RECENT, age=age, entry_id=entry_id))
        age += 1
    return series


def chart_bounds(series: list[ChartSeries], *, live: bool = False) -> ChartBounds:
    """Границы осей для набора линий.

    ``live`` — рендер идёт: по горизонтали показываем только текущий прогон.
    По вертикали масштаб всегда общий, иначе линии не сравнить между собой.
    """
    drawable = [s for s in series if s.points]
    if not drawable:
        return ChartBounds()

    for_x = [s for s in drawable if s.role == ROLE_CURRENT] if live else drawable
    if not for_x:
        for_x = drawable

    spans = [s.frames for s in for_x if s.frames is not None]
    frame_min = min(span[0] for span in spans)
    frame_max = max(span[1] for span in spans)
    seconds_max = max(s.peak for s in drawable)
    return ChartBounds(
        frame_min=frame_min,
        frame_max=frame_max,
        seconds_max=seconds_max if seconds_max > 0 else 0.0,
        empty=False,
    )


def series_summary(series: list[ChartSeries]) -> str:
    """Строка под графиком: что именно нарисовано. На английском, как весь UI."""
    if not series:
        return "No frame times yet"
    parts: list[str] = []
    current = next((s for s in series if s.role == ROLE_CURRENT), None)
    if current is not None:
        # Синяя линия — либо идущий рендер, либо выбранная строка истории:
        # её имя в подписи важнее слова «current».
        parts.append(f"{current.label}: {len(current.points)} frames, peak {current.peak:.1f} s")
    reference = next((s for s in series if s.role == ROLE_REFERENCE), None)
    if reference is not None:
        parts.append(f"reference {reference.label} ({reference.peak:.1f} s peak)")
    recent = [s for s in series if s.role == ROLE_RECENT]
    if recent:
        parts.append(f"{len(recent)} earlier render{'s' if len(recent) > 1 else ''}")
    return " · ".join(parts)


def nice_step(span: float, target: int) -> float:
    """Шаг делений оси: 1, 2 или 5 на порядок — числа, которые читаются с ходу.

    ``target`` — сколько делений хочется получить; шаг округляется вверх, поэтому
    делений выходит не больше запрошенного. Мусор на входе даёт 1.0, а не падение.
    """
    if target <= 0 or not math.isfinite(span) or span <= 0:
        return 1.0
    raw = span / target
    power = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0):
        if raw <= multiple * power:
            return multiple * power
    return 10.0 * power


def value_ticks(high: float, target: int = 5) -> list[float]:
    """Деления оси времени от нуля до потолка. Последнее деление не ниже ``high``.

    Верхнее деление и есть потолок графика: линия упирается в подписанное число,
    а не в произвольный запас над пиком.
    """
    if not math.isfinite(high) or high <= 0:
        return [0.0]
    step = nice_step(high, target)
    count = max(int(math.ceil(high / step - 1e-9)), 1)
    return [round(step * index, 10) for index in range(count + 1)]


def frame_ticks(frame_min: int, frame_max: int, target: int = 5) -> list[int]:
    """Подписи оси кадров. Концы обязательны: по ним видно, какой кусок показан."""
    if frame_max <= frame_min:
        return [frame_min]
    span = frame_max - frame_min
    steps = max(min(target, span), 1)
    return sorted({frame_min + round(span * index / steps) for index in range(steps + 1)})
