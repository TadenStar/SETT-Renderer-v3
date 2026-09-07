"""Карточка «Render Analytics»: три числа о рендере и график времени кадров.

Полосы прогресса живут в ``ui/control_panel.py`` внизу окна — здесь только то,
что помогает судить о скорости: сколько осталось, сколько в среднем занимает
кадр и сколько памяти взял пик. Числа приходят готовыми из
``core.render_stats``, линии графика — из ``core.frame_chart``.
"""
from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from brm.core.frame_chart import ROLE_CURRENT, ChartSeries
from brm.core.render_stats import RenderProgress, format_duration, format_memory
from brm.ui.frame_chart import FrameChart
from brm.ui.theme import set_role

EMPTY = "—"


class StatLabel(QWidget):
    """Подпись и значение в столбик: три таких в ряд образуют шапку карточки."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_label = QLabel(title, self)
        set_role(self.title_label, "muted")
        self.value_label = QLabel(EMPTY, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text or EMPTY)


class AnalyticsPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Render Analytics", parent)
        self.eta_stat = StatLabel("ETA", self)
        self.avg_stat = StatLabel("Avg. frame time", self)
        self.vram_stat = StatLabel("Peak VRAM", self)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(18)
        for stat in (self.eta_stat, self.avg_stat, self.vram_stat):
            stats_row.addWidget(stat)
        stats_row.addStretch(1)

        # Линии прошлых рендеров подставляет главное окно: панель их не знает.
        self._history_series: list[ChartSeries] = []
        self._current_times: list[tuple[int, float]] = []
        self.chart = FrameChart(self)
        self.chart.setToolTip("Frame time, left to right. Blue is this render, red are earlier ones")

        layout = QVBoxLayout(self)
        layout.addLayout(stats_row)
        layout.addWidget(self.chart, 1)
        self.set_idle()

    # --- состояние ---------------------------------------------------------------

    def set_idle(self) -> None:
        for stat in (self.eta_stat, self.avg_stat, self.vram_stat):
            stat.set_value(EMPTY)
        self._current_times = []
        self.chart.clear()

    def set_running(self) -> None:
        self.set_idle()

    def update_progress(self, progress: RenderProgress, elapsed_s: float) -> None:
        eta = progress.eta_seconds()
        self.eta_stat.set_value(format_duration(eta) if eta is not None else "after frame 1")
        average = progress.average_frame_time()
        self.avg_stat.set_value(f"{average:.1f} s" if average else EMPTY)
        self.vram_stat.set_value(format_memory(progress.peak_mb) if progress.peak_mb is not None else EMPTY)
        self.set_current_times(progress.frame_times())
        # Прошедшее время держим в подсказке: в ряду для него места нет,
        # а полоса внизу его и так показывает.
        self.eta_stat.setToolTip(f"Elapsed {format_duration(elapsed_s)}")

    # --- график ------------------------------------------------------------------

    def set_history_series(self, series: list[ChartSeries]) -> None:
        """Линии прошлых рендеров, поверх которых рисуется текущий.

        Сразу перерисовываем: историю могли почистить прямо во время рендера,
        и тогда красные линии обязаны исчезнуть, а синяя — остаться.
        """
        self._history_series = list(series)
        self.set_current_times(self._current_times)

    def set_current_times(self, times: list[tuple[int, float]]) -> None:
        self._current_times = list(times)
        series = list(self._history_series)
        if times:
            series.insert(0, ChartSeries("Current render", tuple(times), ROLE_CURRENT))
        # live=True: по горизонтали видно только текущий прогон, иначе его линия
        # ползла бы по куску оси, растянутой под длинный прошлый рендер.
        self.chart.set_series(series, live=bool(times))
