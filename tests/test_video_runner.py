"""Тесты core/video_runner.py. Вместо ffmpeg запускается python, печатающий прогресс."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from qt_helpers import wait_until

from brm.core.ffmpeg import FfmpegProgress
from brm.core.video_runner import (
    VIDEO_CRASHED,
    VIDEO_FAILED,
    VIDEO_STOPPED,
    VIDEO_SUCCESS,
    VideoProcess,
    describe_result,
)

FAKE_FFMPEG = (
    "import sys, time\n"
    "sys.stderr.write('ffmpeg version 7.1\\n')\n"
    "for f in (1, 2, 3):\n"
    "    sys.stderr.write('frame=%5d fps= 40 q=28.0 time=00:00:0%d.00 speed=1.5x\\r' % (f, f))\n"
    "    sys.stderr.flush()\n"
    f"    time.sleep({0.05})\n"
    "sys.stderr.write('\\nvideo:100kB\\n')\n"
)


@pytest.fixture
def proc(qapp) -> VideoProcess:
    return VideoProcess(kill_delay_ms=200)


def test_progress_is_parsed_from_carriage_returns(qapp, proc: VideoProcess, tmp_path: Path) -> None:
    seen: list[int] = []
    proc.progress_changed.connect(lambda p: seen.append(p.frame))
    proc.start([sys.executable, "-c", FAKE_FFMPEG], total_frames=3, output_file=tmp_path / "out.mp4")
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == VIDEO_SUCCESS and proc.exit_code == 0
    assert seen == [1, 2, 3]
    assert proc.progress.frame == 3 and proc.progress.speed == 1.5 and proc.progress.fraction == 1.0
    assert proc.progress.errors == []
    assert describe_result(proc.status, 0, proc.progress, proc.output_file) == "Video ready: out.mp4 (3 frames)"


def test_failure_is_reported(qapp, proc: VideoProcess, tmp_path: Path) -> None:
    code = "import sys; sys.stderr.write('Error while opening encoder\\n'); sys.exit(1)"
    proc.start([sys.executable, "-c", code], total_frames=3, output_file=tmp_path / "out.mp4")
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == VIDEO_FAILED and proc.exit_code == 1
    assert proc.progress.errors == ["Error while opening encoder"]
    assert "Error while opening encoder" in describe_result(proc.status, 1, proc.progress, proc.output_file)


def test_stop_and_failed_to_start(qapp, proc: VideoProcess, tmp_path: Path) -> None:
    proc.start([sys.executable, "-c", "import time; time.sleep(60)"], total_frames=1)
    wait_until(qapp, proc.is_running, timeout=10)
    with pytest.raises(RuntimeError):
        proc.start([sys.executable, "-c", "pass"])
    proc.stop()
    wait_until(qapp, lambda: proc.status is not None, timeout=20)
    assert proc.status == VIDEO_STOPPED
    assert describe_result(proc.status, None, proc.progress, None) == "Video assembly stopped"

    missing = VideoProcess(kill_delay_ms=200)
    lines: list[str] = []
    missing.line_received.connect(lines.append)
    missing.start([str(tmp_path / "nope" / "ffmpeg.exe"), "-i", "x"], total_frames=1)
    wait_until(qapp, lambda: missing.status is not None, timeout=20)
    assert missing.status == VIDEO_CRASHED
    assert any("could not start" in line for line in lines)
    assert "failed" in describe_result(missing.status, -1, missing.progress, None)


def test_describe_result_without_errors() -> None:
    assert "exit code 7" in describe_result(VIDEO_FAILED, 7, FfmpegProgress(), None)
