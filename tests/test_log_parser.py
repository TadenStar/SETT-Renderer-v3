"""Тесты core/log_parser.py на реальных строках Blender 5.0.1 и старом формате из спеки."""
from __future__ import annotations

from pathlib import Path

import pytest

from brm.core.log_parser import (
    KIND_ANIMATION,
    KIND_BRM,
    KIND_ENGINE,
    KIND_ERROR,
    KIND_FRAME_START,
    KIND_OTHER,
    KIND_PROGRESS,
    KIND_QUIT,
    KIND_SAVED,
    KIND_TIME,
    is_brm_line,
    is_error_line,
    is_out_of_memory,
    parse_line,
    parse_mem,
    parse_time,
    strip_clog_prefix,
)


@pytest.fixture
def logs_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "logs"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("00:12.34", 12.34), ("01:02:03.5", 3723.5), ("7", 7.0), ("x", None), ("1:2:3:4", None)],
)
def test_parse_time(text: str, expected: float | None) -> None:
    assert parse_time(text) == expected


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [("360", "M", 360.0), ("1.5", "G", 1536.0), ("512", "K", 0.5), ("x", "M", None), ("1", "T", None)],
)
def test_parse_mem(value: str, unit: str, expected: float | None) -> None:
    assert parse_mem(value, unit) == expected


def test_strip_clog_prefix() -> None:
    assert strip_clog_prefix("00:01.140  render           | Fra: 1 | x") == "Fra: 1 | x"
    assert strip_clog_prefix("00:01:02.140  render | body") == "body"
    assert strip_clog_prefix("Fra:12 Mem:10M | Sample 1/2") == "Fra:12 Mem:10M | Sample 1/2"


def test_cycles_progress_line() -> None:
    event = parse_line("00:01.547  render           | Fra: 1 | Remaining: 00:00.09 | Mem: 360M | Sample 1/16")
    assert event.kind == KIND_PROGRESS
    assert (event.frame, event.sample, event.samples_total) == (1, 1, 16)
    assert event.mem_mb == 360.0 and event.remaining_s == 0.09
    assert not event.finished


def test_cycles_finished_line() -> None:
    event = parse_line("00:01.625  render           | Fra: 1 | Mem: 360M | Finished")
    assert event.kind == KIND_PROGRESS and event.finished and event.sample is None


def test_eevee_progress_line() -> None:
    event = parse_line("00:00.985  render           | Fra: 1 | Rendering 1 / 8 samples")
    assert event.kind == KIND_PROGRESS
    assert (event.frame, event.sample, event.samples_total) == (1, 1, 8)


def test_legacy_format_from_spec() -> None:
    line = "Fra:12 Mem:1024.55M (Peak 1200.00M) | Time:00:12.34 | Remaining:00:05.00 | Scene, ViewLayer | Sample 128/512"
    event = parse_line(line)
    assert event.kind == KIND_PROGRESS
    assert event.frame == 12 and event.sample == 128 and event.samples_total == 512
    assert event.mem_mb == 1024.55 and event.peak_mb == 1200.0 and event.remaining_s == 5.0


def test_saved_and_time_lines() -> None:
    saved = parse_line("00:01.735  render           | Saved: 'C:\\out\\Куб default\\Scene\\0001.png'")
    assert saved.kind == KIND_SAVED and saved.saved_path == "C:\\out\\Куб default\\Scene\\0001.png"
    timed = parse_line("00:01.735  render           | Time: 00:00.75 (Saving: 00:00.06)")
    assert timed.kind == KIND_TIME and timed.frame_time_s == 0.75 and timed.saving_time_s == 0.06
    plain = parse_line(" Time: 01:05.00")
    assert plain.kind == KIND_TIME and plain.frame_time_s == 65.0 and plain.saving_time_s is None


def test_frame_start_animation_engine_quit() -> None:
    assert parse_line("00:00.860  render           | Rendering frame 2").frame == 2
    assert parse_line("00:00.860  render           | Rendering frame 2").kind == KIND_FRAME_START
    animation = parse_line("00:00.860  render           | Rendering animation (frames 1..3)")
    assert animation.kind == KIND_ANIMATION and (animation.first_frame, animation.last_frame) == (1, 3)
    engine = parse_line("00:00.860  render           | Engine: EEVEE")
    assert engine.kind == KIND_ENGINE and engine.engine == "EEVEE"
    assert parse_line("Blender quit").kind == KIND_QUIT


def test_error_and_brm_kinds() -> None:
    assert parse_line("[BRM] OK   scene = 'Scene'").kind == KIND_BRM
    assert parse_line("[BRM] FAIL scene.cycles.samples: nope").kind == KIND_ERROR
    assert parse_line("00:00.891  reports          | ERROR Cannot render, no camera").kind == KIND_ERROR
    assert parse_line("Error: Cannot read file").kind == KIND_ERROR
    assert parse_line("Traceback (most recent call last):").kind == KIND_ERROR


def test_other_lines_do_not_break() -> None:
    assert parse_line("Blender 5.0.1 (hash a3db93c5b259 built 2025-12-16 01:32:30)").kind == KIND_OTHER
    assert parse_line("").kind == KIND_OTHER
    assert parse_line("ℹ️  blendkit: Verbose is enabled [15:08:37.501, addon_updater.py:150]").kind == KIND_OTHER


def test_out_of_memory_detection() -> None:
    assert is_out_of_memory("CUDA error: Out of memory in cuMemAlloc")
    assert is_out_of_memory("System is out of GPU memory")
    assert not is_out_of_memory("Fra: 1 | Mem: 360M | Sample 1/16")
    assert is_error_line("CUDA error: Out of memory in cuMemAlloc")
    assert is_brm_line("[BRM] override applied: ok=3 skip=0 fail=0")


def test_successful_log_has_no_errors(logs_dir: Path) -> None:
    lines = (logs_dir / "eevee_single_frame_5.0.1.log").read_text(encoding="utf-8").splitlines()
    assert not [line for line in lines if is_error_line(line)]
    assert lines[-1] == "# finished: status=success exit_code=0"


def test_no_camera_log_reports_exactly_one_error(logs_dir: Path) -> None:
    lines = (logs_dir / "no_camera_error_5.0.1.log").read_text(encoding="utf-8").splitlines()
    errors = [line for line in lines if is_error_line(line)]
    assert len(errors) == 1 and "no camera" in errors[0]
