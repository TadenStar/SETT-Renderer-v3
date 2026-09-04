"""Тесты core/frame_chart.py: какие линии рисуем и в каких границах."""
from __future__ import annotations

import pytest

from brm.core.frame_chart import (
    DEFAULT_RECENT,
    ROLE_CURRENT,
    ROLE_RECENT,
    ROLE_REFERENCE,
    ChartData,
    ChartSeries,
    build_series,
    chart_bounds,
    series_summary,
)

CURRENT = [(1, 2.0), (2, 3.0), (3, 2.5)]


def history(count: int, *, start_id: int = 100) -> list[tuple[int, str, list[tuple[int, float]]]]:
    """Прогоны от новых к старым, как отдаёт HistoryStore."""
    return [
        (start_id - i, f"run {i}", [(frame, 1.0 + i) for frame in range(1, 5)])
        for i in range(count)
    ]


def test_current_render_comes_first_and_is_blue() -> None:
    series = build_series(CURRENT, history(2))
    assert series[0].role == ROLE_CURRENT and series[0].points == tuple(CURRENT)
    assert [s.role for s in series[1:]] == [ROLE_RECENT, ROLE_RECENT]


def test_only_the_last_five_earlier_renders_are_kept() -> None:
    """Больше пяти линий читаются как каша."""
    series = build_series(None, history(9))
    recent = [s for s in series if s.role == ROLE_RECENT]
    assert len(recent) == DEFAULT_RECENT
    assert [s.age for s in recent] == [0, 1, 2, 3, 4]
    assert [s.label for s in recent] == [f"run {i}" for i in range(5)]  # самые свежие


def test_reference_survives_ageing_and_does_not_take_a_slot() -> None:
    """Эталон рисуется всегда, даже когда он давно вышел из последних пяти."""
    runs = history(9)
    old_id = runs[8][0]
    series = build_series(None, runs, reference_id=old_id)
    reference = [s for s in series if s.role == ROLE_REFERENCE]
    assert len(reference) == 1 and reference[0].entry_id == old_id
    # Эталон не занял место среди последних: их по-прежнему пять.
    assert len([s for s in series if s.role == ROLE_RECENT]) == DEFAULT_RECENT


def test_runs_without_frame_times_are_skipped() -> None:
    """Упавший до первого кадра рендер рисовать нечем."""
    runs = [(1, "broken", []), (2, "good", [(1, 1.0)])]
    series = build_series(None, runs)
    assert [s.label for s in series] == ["good"]


def test_empty_input_draws_nothing() -> None:
    assert build_series(None, []) == []
    assert build_series([], []) == []
    bounds = chart_bounds([])
    assert bounds.empty and bounds.frame_span == 0
    assert series_summary([]) == "No frame times yet"


def test_bounds_cover_everything_when_idle() -> None:
    """В покое оси охватывают все линии — иначе короткий прогон не сравнить с длинным."""
    series = build_series(CURRENT, [(1, "long", [(f, 5.0) for f in range(1, 21)])])
    bounds = chart_bounds(series, live=False)
    assert (bounds.frame_min, bounds.frame_max) == (1, 20)
    assert bounds.seconds_max == 5.0


def test_during_a_render_the_x_axis_shows_only_the_current_run() -> None:
    """Иначе идущий рендер полз бы по куску оси, растянутой под длинный прошлый."""
    series = build_series(CURRENT, [(1, "long", [(f, 5.0) for f in range(1, 21)])])
    bounds = chart_bounds(series, live=True)
    assert (bounds.frame_min, bounds.frame_max) == (1, 3)  # только текущий
    assert bounds.seconds_max == 5.0  # по вертикали масштаб всё равно общий


def test_live_bounds_fall_back_when_there_is_no_current_run() -> None:
    series = build_series(None, history(1))
    bounds = chart_bounds(series, live=True)
    assert not bounds.empty and (bounds.frame_min, bounds.frame_max) == (1, 4)


def test_drawing_order_puts_the_current_run_on_top() -> None:
    series = build_series(CURRENT, history(3), reference_id=98)
    order = [s.role for s in ChartData(series).visible()]
    assert order[-1] == ROLE_CURRENT  # синяя линия поверх всего
    assert order.index(ROLE_REFERENCE) > order.index(ROLE_RECENT)
    ages = [s.age for s in ChartData(series).visible() if s.role == ROLE_RECENT]
    assert ages == sorted(ages, reverse=True)  # старое рисуется первым, снизу


@pytest.mark.parametrize("count, expected", [(1, "1 earlier render"), (3, "3 earlier renders")])
def test_summary_describes_what_is_drawn(count: int, expected: str) -> None:
    series = build_series(CURRENT, history(count))
    summary = series_summary(series)
    assert "Current render: 3 frames, peak 3.0 s" in summary and expected in summary


def test_summary_names_the_reference() -> None:
    summary = series_summary(build_series(None, history(3), reference_id=98))
    assert "reference run 2" in summary


def test_series_peak_and_frames_handle_a_single_point() -> None:
    one = ChartSeries("x", ((7, 1.5),), ROLE_CURRENT)
    assert one.peak == 1.5 and one.frames == (7, 7)
    assert ChartSeries("empty", (), ROLE_RECENT).frames is None
