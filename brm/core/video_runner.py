"""Запуск ffmpeg через QProcess: живой прогресс сборки видео (раздел 4.8 спеки).

Аналог ``RenderProcess``, но для ffmpeg: прогресс приходит в stderr строками
с ``frame=``, перезаписываемыми через ``\\r``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from brm.core.ffmpeg import FfmpegProgress, SequenceInfo, VideoPreset, parse_ffmpeg_line

log = logging.getLogger(__name__)

_LINE_SPLIT = re.compile(r"\r\n|\r|\n")

VIDEO_SUCCESS = "success"
VIDEO_FAILED = "failed"
VIDEO_STOPPED = "stopped"
VIDEO_CRASHED = "crashed"


class VideoProcess(QObject):
    """Один запуск ffmpeg: старт, построчный вывод, прогресс, Stop."""

    started = Signal()
    line_received = Signal(str)
    progress_changed = Signal(object)  # FfmpegProgress
    finished = Signal(int, str)  # код выхода, статус

    def __init__(self, parent: QObject | None = None, *, kill_delay_ms: int = 5000) -> None:
        super().__init__(parent)
        self._kill_delay_ms = kill_delay_ms
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_ready_read)
        self._proc.started.connect(self.started)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._buffer = ""
        self._stop_requested = False
        self._reported = False
        self.argv: list[str] = []
        self.output_file: Path | None = None
        self.progress = FfmpegProgress()
        self.status: str | None = None
        self.exit_code: int | None = None

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, argv: list[str], *, total_frames: int = 0, output_file: Path | str | None = None) -> None:
        if self.is_running():
            raise RuntimeError("ffmpeg is already running")
        self.argv = list(argv)
        self.output_file = Path(output_file) if output_file else Path(argv[-1])
        self.progress = FfmpegProgress(total_frames=total_frames)
        self._buffer = ""
        self._stop_requested = False
        self._reported = False
        self.status = None
        self.exit_code = None
        self._proc.setProgram(argv[0])
        self._proc.setArguments(argv[1:])
        self._proc.start()

    def stop(self) -> None:
        if not self.is_running():
            return
        self._stop_requested = True
        self._proc.terminate()
        QTimer.singleShot(self._kill_delay_ms, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.is_running():
            self._proc.kill()

    # --- вывод -------------------------------------------------------------------------

    def _on_ready_read(self) -> None:
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer += data
        parts = _LINE_SPLIT.split(self._buffer)
        self._buffer = parts.pop()
        for line in parts:
            self._emit_line(line)

    def _emit_line(self, line: str) -> None:
        self.line_received.emit(line)
        if parse_ffmpeg_line(line, self.progress):
            self.progress_changed.emit(self.progress)

    def _flush(self) -> None:
        if self._buffer:
            tail, self._buffer = self._buffer, ""
            self._emit_line(tail)

    # --- завершение ------------------------------------------------------------------

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._flush()
        if self._stop_requested:
            status = VIDEO_STOPPED
        elif exit_status == QProcess.ExitStatus.CrashExit:
            status = VIDEO_CRASHED
        elif exit_code == 0:
            status = VIDEO_SUCCESS
        else:
            status = VIDEO_FAILED
        self._report(exit_code, status)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            message = f"could not start {self._proc.program()}: {self._proc.errorString()}"
            self.progress.errors.append(message)
            self._emit_line(f"[BRM] FAIL {message}")
            self._report(-1, VIDEO_CRASHED)

    def _report(self, exit_code: int, status: str) -> None:
        if self._reported:
            return
        self._reported = True
        self.exit_code = exit_code
        self.status = status
        self.finished.emit(exit_code, status)


def describe_result(status: str, exit_code: int | None, progress: FfmpegProgress, output_file: Path | None) -> str:
    """Текст для UI по итогу сборки."""
    if status == VIDEO_SUCCESS:
        name = output_file.name if output_file else "video"
        return f"Video ready: {name} ({progress.frame} frames)"
    if status == VIDEO_STOPPED:
        return "Video assembly stopped"
    detail = progress.errors[-1] if progress.errors else f"exit code {exit_code}"
    return f"Video assembly failed: {detail}"


def build_and_check(
    ffmpeg_path: str,
    sequence: SequenceInfo,
    preset: VideoPreset,
    output_file: Path,
    *,
    fps: float,
) -> list[str]:
    """Обёртка над сборкой argv, чтобы UI не импортировал сразу два модуля."""
    from brm.core.ffmpeg import build_ffmpeg_argv

    return build_ffmpeg_argv(ffmpeg_path, sequence, preset, output_file, fps=fps)
