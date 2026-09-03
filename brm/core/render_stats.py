"""Состояние рендера из событий лога: кадры, сэмплы, память, ETA, время кадров.

ETA считается по скользящему среднему последних пяти кадров, а не по
``Remaining`` от Blender: тот про текущий кадр (раздел 6 спеки). Время кадров
копится в ``frame_stats`` — это данные для графика времени кадров и истории.
Модуль без Qt.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from brm.core.log_parser import (
    KIND_ANIMATION,
    KIND_BRM,
    KIND_ENGINE,
    KIND_ERROR,
    KIND_FRAME_START,
    KIND_PROGRESS,
    KIND_SAVED,
    KIND_TIME,
    LogEvent,
    is_out_of_memory,
    parse_line,
)

ETA_WINDOW = 5
_FRAME_IN_NAME = re.compile(r"(\d+)\.[A-Za-z0-9]+$")


@dataclass
class FrameStat:
    frame: int
    render_time_s: float | None = None  # «Time:» от Blender: чистый рендер кадра
    wall_time_s: float | None = None  # по нашим часам: от старта кадра до Saved
    saved_path: str | None = None


@dataclass
class RenderProgress:
    frames_expected: list[int] = field(default_factory=list)
    frames_done: list[int] = field(default_factory=list)
    current_frame: int | None = None
    sample: int | None = None
    samples_total: int | None = None
    mem_mb: float | None = None
    peak_mb: float | None = None
    remaining_frame_s: float | None = None
    engine: str | None = None
    errors: list[str] = field(default_factory=list)
    brm_lines: list[str] = field(default_factory=list)
    frame_stats: list[FrameStat] = field(default_factory=list)
    last_saved: str | None = None

    @property
    def frames_total(self) -> int:
        return len(self.frames_expected)

    @property
    def frames_done_count(self) -> int:
        return len(self.frames_done)

    @property
    def frame_fraction(self) -> float:
        return self.frames_done_count / self.frames_total if self.frames_total else 0.0

    @property
    def sample_fraction(self) -> float:
        if not self.samples_total:
            return 0.0
        return min(max((self.sample or 0) / self.samples_total, 0.0), 1.0)

    def remaining_frames(self) -> list[int]:
        done = set(self.frames_done)
        return [f for f in self.frames_expected if f not in done]

    def frame_times(self) -> list[tuple[int, float]]:
        """(кадр, секунды) для графика: время рендера, а без него — по часам."""
        result = []
        for stat in self.frame_stats:
            seconds = stat.render_time_s if stat.render_time_s is not None else stat.wall_time_s
            if seconds is not None:
                result.append((stat.frame, seconds))
        return result

    def average_frame_time(self, window: int = ETA_WINDOW) -> float | None:
        """Скользящее среднее по последним кадрам; по часам, если есть, иначе по Time:."""
        samples = []
        for stat in self.frame_stats[-window:]:
            seconds = stat.wall_time_s if stat.wall_time_s is not None else stat.render_time_s
            if seconds is not None:
                samples.append(seconds)
        return sum(samples) / len(samples) if samples else None

    def eta_seconds(self) -> float | None:
        average = self.average_frame_time()
        if average is None or not self.frames_total:
            return None
        return average * len(self.remaining_frames())

    def has_out_of_memory(self) -> bool:
        return any(is_out_of_memory(line) for line in self.errors)


class RenderTracker:
    """Кормится строками лога и держит ``RenderProgress``. Часы подменяемы для тестов."""

    def __init__(self, expected_frames: Iterable[int], clock: Callable[[], float] = time.monotonic) -> None:
        self.progress = RenderProgress(frames_expected=list(expected_frames))
        self._clock = clock
        self._frame_started_at: float | None = None
        self._frame_started_for: int | None = None

    def feed(self, line: str) -> LogEvent:
        event = parse_line(line)
        progress = self.progress
        kind = event.kind
        if kind == KIND_BRM:
            progress.brm_lines.append(event.raw)
        elif kind == KIND_ERROR:
            progress.errors.append(event.raw)
        elif kind == KIND_ANIMATION and not progress.frames_expected:
            assert event.first_frame is not None and event.last_frame is not None
            progress.frames_expected = list(range(event.first_frame, event.last_frame + 1))
        elif kind == KIND_ENGINE:
            progress.engine = event.engine
        elif kind == KIND_FRAME_START:
            self._begin_frame(event.frame)
        elif kind == KIND_PROGRESS:
            if event.frame != self._frame_started_for:
                self._begin_frame(event.frame)
            if event.sample is not None:
                progress.sample, progress.samples_total = event.sample, event.samples_total
            if event.mem_mb is not None:
                progress.mem_mb = event.mem_mb
                progress.peak_mb = max(progress.peak_mb or 0.0, event.mem_mb)
            if event.peak_mb is not None:
                progress.peak_mb = max(progress.peak_mb or 0.0, event.peak_mb)
            if event.remaining_s is not None:
                progress.remaining_frame_s = event.remaining_s
        elif kind == KIND_SAVED:
            self._frame_saved(event.saved_path or "")
        elif kind == KIND_TIME and progress.frame_stats:
            progress.frame_stats[-1].render_time_s = event.frame_time_s
        return event

    def remaining_frames(self) -> list[int]:
        return self.progress.remaining_frames()

    def _begin_frame(self, frame: int | None) -> None:
        progress = self.progress
        progress.current_frame = frame
        progress.sample = None
        progress.remaining_frame_s = None
        self._frame_started_at = self._clock()
        self._frame_started_for = frame
        if frame is not None and frame not in progress.frames_expected:
            progress.frames_expected.append(frame)

    def _frame_saved(self, path: str) -> None:
        progress = self.progress
        frame = progress.current_frame
        if frame is None:
            match = _FRAME_IN_NAME.search(path)
            frame = int(match.group(1)) if match else len(progress.frames_done) + 1
        wall = None if self._frame_started_at is None else self._clock() - self._frame_started_at
        progress.frame_stats.append(FrameStat(frame=frame, wall_time_s=wall, saved_path=path))
        if frame not in progress.frames_done:
            progress.frames_done.append(frame)
        if frame not in progress.frames_expected:
            progress.frames_expected.append(frame)
        progress.last_saved = path
        progress.sample = None
        self._frame_started_at = None
        self._frame_started_for = None


def diagnose_failure(errors: list[str], exit_code: int | None, status: str) -> str | None:
    """Конкретная подсказка вместо абстрактной «ошибка рендера» (раздел 6 спеки)."""
    text = "\n".join(errors)
    if any(is_out_of_memory(line) for line in errors):
        return (
            "Out of memory. Lower Texture Limit (Simplify), reduce the tile size, "
            "disable Persistent Data, or move denoising to the CPU"
        )
    if "no camera" in text.lower():
        return "The scene has no active camera. Set one in the scene or pick another scene"
    if "Cannot read file" in text:
        return "Blender cannot read the .blend file. Check the path and the file version"
    if "CUDA error" in text or "OptiX error" in text:
        return "GPU error. Try the CPU device or update the NVIDIA driver"
    if "[BRM] FAIL" in text or "Traceback" in text:
        return "The override script failed. Check the [BRM] lines in the log"
    if status == "failed" and exit_code:
        return f"Blender exited with code {exit_code}. Check the Errors filter in the log"
    return None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(int(round(seconds)), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs} s"


def format_memory(mb: float | None) -> str:
    if mb is None:
        return "—"
    return f"{mb / 1024:.2f} G" if mb >= 1024 else f"{mb:.0f} M"


def parse_log_file(path: str | os.PathLike[str], expected_frames: Iterable[int] = ()) -> RenderProgress:
    """Старые логи тоже разбираются: график времени кадров по прошедшим рендерам."""
    tracker = RenderTracker(expected_frames)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            tracker.feed(line)
    return tracker.progress


def stats_dict(progress: RenderProgress, *, status: str, exit_code: int | None, duration_s: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 3),
        "engine": progress.engine,
        "frames_expected": progress.frames_expected,
        "frames_done": progress.frames_done,
        "peak_mem_mb": progress.peak_mb,
        "frame_stats": [asdict(stat) for stat in progress.frame_stats],
        "errors": progress.errors,
    }
    if extra:
        data.update(extra)
    return data


def write_stats(path: str | os.PathLike[str], data: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return target
