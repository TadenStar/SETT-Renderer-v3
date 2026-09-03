"""Тесты core/blender_process.py. Вместо Blender запускается текущий python."""
from __future__ import annotations

import sys
import threading

import pytest

from brm.core.blender_process import (
    BlenderResult,
    describe_failure,
    run_blender,
    script_path,
)


def test_run_captures_output_and_brm_lines() -> None:
    code = "print('noise'); print('[BRM] hello'); print('Привет, кириллица')"
    result = run_blender(sys.executable, ["-c", code], timeout=30)
    assert result.ok
    assert result.returncode == 0
    assert result.brm_lines() == ["[BRM] hello"]
    assert "кириллица" in result.stdout
    assert result.argv[0] == sys.executable


def test_nonzero_exit_is_not_ok() -> None:
    result = run_blender(sys.executable, ["-c", "import sys; print('boom'); sys.exit(3)"], timeout=30)
    assert not result.ok
    assert result.returncode == 3
    assert "exit code 3" in describe_failure("Probe", result)
    assert "boom" in describe_failure("Probe", result)


def test_timeout_kills_process() -> None:
    result = run_blender(sys.executable, ["-c", "import time; time.sleep(30)"], timeout=0.5, poll_interval=0.05)
    assert result.timed_out
    assert not result.ok
    assert result.duration < 10
    assert "timed out" in describe_failure("Probe", result)


def test_cancel_event_kills_process() -> None:
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    result = run_blender(sys.executable, ["-c", "import time; time.sleep(30)"], timeout=30, cancel=cancel, poll_interval=0.05)
    assert result.cancelled
    assert not result.ok
    assert result.duration < 10
    assert "cancelled" in describe_failure("Probe", result)


def test_stderr_is_merged_into_stdout() -> None:
    result = run_blender(sys.executable, ["-c", "import sys; sys.stderr.write('[BRM] from stderr\\n')"], timeout=30)
    assert "[BRM] from stderr" in result.brm_lines()


def test_tail_returns_last_lines() -> None:
    result = BlenderResult(argv=[], returncode=0, stdout="a\nb\nc\nd", duration=0)
    assert result.tail(2) == "c\nd"


@pytest.mark.parametrize("name", ["probe_caps.py", "probe_scene.py"])
def test_script_path_points_to_existing_scripts(name: str) -> None:
    assert script_path(name).is_file()


def test_script_path_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        script_path("nope.py")
