"""Тесты core/render_stats.py: трекер на реальных логах, ETA, подсказки, статистика."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.render_stats import (
    RenderTracker,
    diagnose_failure,
    format_duration,
    format_memory,
    parse_log_file,
    stats_dict,
    write_stats,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def logs_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "logs"


def feed_file(tracker: RenderTracker, path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        tracker.feed(line)


def test_tracker_on_cycles_animation(logs_dir: Path) -> None:
    tracker = RenderTracker([1, 2, 3])
    feed_file(tracker, logs_dir / "cycles_anim_3_frames_5.0.1.log")
    progress = tracker.progress
    assert progress.frames_done == [1, 2, 3]
    assert progress.remaining_frames() == []
    assert progress.engine == "Cycles"
    assert progress.samples_total == 16
    assert progress.peak_mb == 360.0
    assert progress.frame_times() == [(1, 0.75), (2, 0.6), (3, 0.58)]
    assert progress.errors == []
    assert any("override applied" in line for line in progress.brm_lines)
    assert progress.frame_fraction == 1.0


def test_tracker_learns_frames_from_animation_line(logs_dir: Path) -> None:
    tracker = RenderTracker([])
    feed_file(tracker, logs_dir / "eevee_anim_3_frames_5.0.1.log")
    progress = tracker.progress
    assert progress.frames_expected == [1, 2, 3]
    assert progress.engine == "EEVEE"
    assert progress.samples_total == 8
    assert progress.frame_times() == [(1, 0.19), (2, 0.03), (3, 0.03)]


def test_tracker_on_no_camera_failure(logs_dir: Path) -> None:
    tracker = RenderTracker([0])
    feed_file(tracker, logs_dir / "no_camera_error_5.0.1.log")
    progress = tracker.progress
    assert progress.frames_done == []
    assert progress.remaining_frames() == [0]
    assert len(progress.errors) == 1 and "no camera" in progress.errors[0]
    assert "camera" in diagnose_failure(progress.errors, 1, "failed")


def test_eta_uses_wall_clock_moving_average() -> None:
    clock = FakeClock()
    tracker = RenderTracker(range(1, 11), clock=clock)
    assert tracker.progress.eta_seconds() is None
    for frame, seconds in ((1, 2.0), (2, 4.0), (3, 6.0)):
        tracker.feed(f"00:00.000  render | Rendering frame {frame}")
        tracker.feed(f"00:00.000  render | Fra: {frame} | Mem: 10M | Sample 8/16")
        clock.advance(seconds)
        tracker.feed(f"00:00.000  render | Saved: 'D:/out/{frame:04d}.png'")
        tracker.feed("00:00.000  render | Time: 00:01.00 (Saving: 00:00.00)")
    progress = tracker.progress
    assert progress.frames_done == [1, 2, 3]
    assert progress.average_frame_time() == 4.0
    assert progress.eta_seconds() == 4.0 * 7
    assert progress.frame_fraction == 0.3
    assert [wall for _, wall in ((s.frame, s.wall_time_s) for s in progress.frame_stats)] == [2.0, 4.0, 6.0]
    assert progress.frame_times() == [(1, 1.0), (2, 1.0), (3, 1.0)]  # для графика — Time: от Blender


def test_eta_window_is_five_frames() -> None:
    clock = FakeClock()
    tracker = RenderTracker(range(1, 9), clock=clock)
    for frame in range(1, 8):
        tracker.feed(f"Rendering frame {frame}")
        clock.advance(float(frame))
        tracker.feed(f"Saved: 'x/{frame:04d}.png'")
    assert tracker.progress.average_frame_time() == 5.0  # среднее из 3..7
    assert tracker.progress.eta_seconds() == 5.0


def test_frame_number_taken_from_saved_path_when_unknown() -> None:
    tracker = RenderTracker([])
    tracker.feed("Saved: 'D:/out/shot/0007.png'")
    assert tracker.progress.frames_done == [7]
    assert tracker.progress.frames_expected == [7]


def test_sample_state_resets_on_new_frame() -> None:
    tracker = RenderTracker([1, 2])
    tracker.feed("Fra: 1 | Mem: 100M | Sample 8/16")
    assert tracker.progress.sample_fraction == 0.5 and tracker.progress.current_frame == 1
    tracker.feed("Fra: 2 | Mem: 120M | Sample 0/16")
    assert tracker.progress.current_frame == 2 and tracker.progress.sample == 0
    assert tracker.progress.peak_mb == 120.0


def test_peak_memory_from_explicit_and_running_values() -> None:
    tracker = RenderTracker([12])
    tracker.feed("Fra:12 Mem:1024.55M (Peak 1200.00M) | Time:00:12.34 | Remaining:00:05.00 | Scene, ViewLayer | Sample 128/512")
    assert tracker.progress.peak_mb == 1200.0 and tracker.progress.remaining_frame_s == 5.0
    tracker.feed("Fra:12 Mem:1300M | Sample 200/512")
    assert tracker.progress.peak_mb == 1300.0


@pytest.mark.parametrize(
    ("errors", "exit_code", "status", "fragment"),
    [
        (["CUDA error: Out of memory in cuMemAlloc"], 1, "failed", "Out of memory"),
        (["00:00.891  reports          | ERROR Cannot render, no camera"], 1, "failed", "no active camera"),
        (["Error: Cannot read file 'x.blend'"], 1, "failed", "cannot read"),
        (["OptiX error: OPTIX_ERROR_UNKNOWN"], 1, "failed", "GPU error"),
        (["[BRM] FAIL scene.cycles.samples: nope"], 1, "failed", "override script"),
        ([], 11, "failed", "code 11"),
    ],
)
def test_diagnose_failure(errors: list[str], exit_code: int, status: str, fragment: str) -> None:
    assert fragment in diagnose_failure(errors, exit_code, status)


def test_diagnose_failure_none_on_success() -> None:
    assert diagnose_failure([], 0, "success") is None


def test_format_duration_and_memory() -> None:
    assert format_duration(None) == "—"
    assert format_duration(42) == "42 s"
    assert format_duration(725) == "12m 05s"
    assert format_duration(5025) == "1h 23m 45s"
    assert format_memory(360) == "360 M"
    assert format_memory(2048) == "2.00 G"
    assert format_memory(None) == "—"


def test_parse_log_file_for_frame_time_chart(logs_dir: Path) -> None:
    progress = parse_log_file(logs_dir / "cycles_anim_3_frames_5.0.1.log")
    assert progress.frame_times() == [(1, 0.75), (2, 0.6), (3, 0.58)]
    assert progress.frames_expected == [1, 2, 3]


def test_stats_dict_and_write(tmp_path: Path, logs_dir: Path) -> None:
    progress = parse_log_file(logs_dir / "eevee_anim_3_frames_5.0.1.log")
    data = stats_dict(progress, status="success", exit_code=0, duration_s=1.234, extra={"scene": "Scene"})
    path = write_stats(tmp_path / "deep" / "render_stats_x.json", data)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["status"] == "success" and loaded["scene"] == "Scene"
    assert loaded["frames_done"] == [1, 2, 3]
    assert loaded["frame_stats"][0]["render_time_s"] == 0.19
    assert loaded["engine"] == "EEVEE"


def test_frames_are_counted_when_output_is_glued_to_the_log() -> None:
    """Регрессия с реального рендера: счётчик кадров стоял на нуле.

    Таблица --cycles-print-stats врезалась в строку «Saved:», та переставала
    начинаться с начала строки, и трекер не засчитывал ни одного кадра —
    ни прогресса, ни ETA, ни точек на графике.
    """
    lines = [
        "00:00.100  render           | Fra: 0 | Mem: 2303M | Sample 1/128",
        r"        brick.jpg   16.00M (16,777,200:20.234  render           | Saved: 'C:\out\0000.jpg'",
        "00:20.234  render           | Time: 00:19.28 (Saving: 00:00.14)",
        "00:20.300  render           | Fra: 1 | Mem: 2316M | Sample 1/128",
        r"        wood.png    64.00M (67,108,800:36.187  render           | Saved: 'C:\out\0001.jpg'",
        "00:36.187  render           | Time: 00:15.95 (Saving: 00:00.05)",
    ]
    tracker = RenderTracker(range(0, 3))
    for line in lines:
        tracker.feed(line)

    progress = tracker.progress
    assert progress.frames_done == [0, 1]
    assert progress.frames_done_count == 2
    # Время кадра тоже доезжает: без Saved оно раньше отбрасывалось.
    assert [round(t, 2) for _, t in progress.frame_times()] == [19.28, 15.95]
    assert progress.eta_seconds() is not None
