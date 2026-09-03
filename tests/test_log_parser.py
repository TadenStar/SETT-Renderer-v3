"""Тесты core/log_parser.py на сохранённых сырых логах Blender 5.0.1 (tests/fixtures/logs)."""
from __future__ import annotations

from pathlib import Path

import pytest

from brm.core.log_parser import is_brm_line, is_error_line


@pytest.fixture
def logs_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "logs"


@pytest.mark.parametrize(
    "line",
    [
        "Error: Cannot read file",
        "00:00.891  reports          | ERROR Cannot render, no camera",
        "Traceback (most recent call last):",
        "CUDA error at cuCtxCreate: Out of memory",
        "OptiX error: OPTIX_ERROR_UNKNOWN",
        "[BRM] FAIL scene.cycles.samples: nope",
    ],
)
def test_error_lines(line: str) -> None:
    assert is_error_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "00:01.140  render           | Fra: 1 | Rendering 25 / 64 samples",
        "[BRM] OK   scene = 'Scene'",
        "Blender quit",
        "ℹ️  blendkit: Verbose is enabled [15:08:37.501, addon_updater.py:150]",
    ],
)
def test_normal_lines(line: str) -> None:
    assert not is_error_line(line)


def test_brm_prefix() -> None:
    assert is_brm_line("[BRM] override applied: ok=3 skip=0 fail=0")
    assert not is_brm_line("00:00.891  render           | Rendering frame 0")


def test_successful_log_has_no_errors(logs_dir: Path) -> None:
    lines = (logs_dir / "eevee_single_frame_5.0.1.log").read_text(encoding="utf-8").splitlines()
    assert not [line for line in lines if is_error_line(line)]
    assert any("Saved:" in line for line in lines)
    assert lines[-1] == "# finished: status=success exit_code=0"


def test_no_camera_log_reports_exactly_one_error(logs_dir: Path) -> None:
    lines = (logs_dir / "no_camera_error_5.0.1.log").read_text(encoding="utf-8").splitlines()
    errors = [line for line in lines if is_error_line(line)]
    assert len(errors) == 1 and "no camera" in errors[0]
    assert lines[-1] == "# finished: status=failed exit_code=1"
