"""Тесты core/runner.py. Вместо Blender запускается текущий python."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from qt_helpers import wait_until

from brm.core.command_builder import command_line
from brm.core.models import RenderJob
from brm.core.render_plan import RenderPlan
from brm.core.runner import (
    STATUS_CRASHED,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
    RenderProcess,
)


def make_plan(tmp_path: Path, argv: list[str]) -> RenderPlan:
    out = tmp_path / "out"
    return RenderPlan(
        job=RenderJob(blend_path="x.blend"),
        argv=argv,
        command_line=command_line(argv),
        override_script=tmp_path / "override.py",
        override_settings={},
        output_path=str(out / "####"),
        output_dir=out,
        frames=[1],
        engine="CYCLES",
        cycles_device=None,
        log_path=out / "render_log_test.txt",
    )


def python_plan(tmp_path: Path, code: str) -> RenderPlan:
    return make_plan(tmp_path, [sys.executable, "-c", code])


@pytest.fixture
def proc(qapp) -> RenderProcess:
    return RenderProcess(kill_delay_ms=200)


def test_lines_are_split_on_cr_and_lf_and_logged(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    code = (
        "import sys; sys.stdout.write('Fra:1 Sample 1/4\\rFra:1 Sample 2/4\\rFra:1 Sample 4/4\\n"
        "Saved: \\'x\\'\\n[BRM] OK   scene = Сцена'); sys.stdout.flush()"
    )
    lines: list[str] = []
    proc.line_received.connect(lines.append)
    proc.start(python_plan(tmp_path, code))
    wait_until(qapp, lambda: proc.status is not None, timeout=20)

    assert lines == [
        "Fra:1 Sample 1/4",
        "Fra:1 Sample 2/4",
        "Fra:1 Sample 4/4",
        "Saved: 'x'",
        "[BRM] OK   scene = Сцена",
    ]
    assert proc.status == STATUS_SUCCESS and proc.exit_code == 0
    log_text = (tmp_path / "out" / "render_log_test.txt").read_text(encoding="utf-8")
    assert log_text.startswith("# BRM render log")
    assert "# command: " in log_text
    assert "[BRM] OK   scene = Сцена\n" in log_text
    assert log_text.rstrip().endswith("# finished: status=success exit_code=0")


def test_nonzero_exit_is_failed(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    proc.start(python_plan(tmp_path, "import sys; print('Error: boom'); sys.exit(3)"))
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == STATUS_FAILED and proc.exit_code == 3


def test_stderr_is_merged(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    lines: list[str] = []
    proc.line_received.connect(lines.append)
    proc.start(python_plan(tmp_path, "import sys; sys.stderr.write('Traceback (most recent call last)\\n')"))
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert "Traceback (most recent call last)" in lines


def test_stop_terminates_then_kills(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    proc.start(python_plan(tmp_path, "import time; print('started', flush=True); time.sleep(60)"))
    wait_until(qapp, proc.is_running, timeout=20)
    proc.stop()
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == STATUS_STOPPED
    assert not proc.is_running()
    log_text = (tmp_path / "out" / "render_log_test.txt").read_text(encoding="utf-8")
    assert "status=stopped" in log_text


def test_failed_to_start_is_reported_as_crashed(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    lines: list[str] = []
    proc.line_received.connect(lines.append)
    proc.start(make_plan(tmp_path, [str(tmp_path / "nope" / "blender.exe"), "-b"]))
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == STATUS_CRASHED and proc.exit_code == -1
    assert any(line.startswith("[BRM] FAIL could not start") for line in lines)


def test_cannot_start_while_running(qapp, proc: RenderProcess, tmp_path: Path) -> None:
    proc.start(python_plan(tmp_path, "import time; time.sleep(60)"))
    wait_until(qapp, proc.is_running, timeout=20)
    with pytest.raises(RuntimeError):
        proc.start(python_plan(tmp_path, "print(1)"))
    proc.stop()
    wait_until(qapp, lambda: proc.status is not None, timeout=20)


def test_stop_when_idle_is_noop(qapp, proc: RenderProcess) -> None:
    proc.stop()
    assert proc.status is None
