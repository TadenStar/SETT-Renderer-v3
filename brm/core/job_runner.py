"""Оркестратор задачи (M5): resume по папке вывода, пачки, авторетрай, пауза после кадра.

Второе и последнее место в core с импортом Qt: сигналы и один ``RenderProcess``
за раз. Планирование (скан, пачки, шаги ретрая) — в чистых модулях
``output_scan`` и ``chunking``. Трекер общий на всю задачу: прогресс, ETA и
график времени кадров не обнуляются между пачками и после Resume.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from brm.core.capabilities import Capabilities
from brm.core.chunking import MAX_CRASH_RETRIES, describe_chunk, oom_retry_overrides, split_chunks
from brm.core.log_parser import KIND_TIME, LogEvent, is_out_of_memory
from brm.core.models import RenderJob
from brm.core.output_scan import extension_for_format, scan_output
from brm.core.project_probe import ProjectInfo
from brm.core.render_plan import RenderPlan, build_render_plan
from brm.core.render_stats import RenderTracker, stats_dict, write_stats
from brm.core.runner import STATUS_STOPPED, RenderProcess
from brm.core.storage import AppSettings

log = logging.getLogger(__name__)

RUN_SUCCESS = "success"
RUN_FAILED = "failed"
RUN_STOPPED = "stopped"
RUN_PAUSED = "paused"


class JobRunner(QObject):
    started = Signal()
    chunk_started = Signal(object)  # RenderPlan
    line_received = Signal(str)
    event_received = Signal(object)  # LogEvent после трекера
    finished = Signal(str)  # RUN_*

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        plan_builder: Callable[..., RenderPlan] | None = None,
        process_factory: Callable[[], RenderProcess] | None = None,
        kill_delay_ms: int = 5000,
    ) -> None:
        super().__init__(parent)
        # Подстановка на момент создания, чтобы тесты могли подменить build_render_plan.
        self._plan_builder = plan_builder or build_render_plan
        self._process_factory = process_factory or (lambda: RenderProcess(self, kill_delay_ms=kill_delay_ms))
        self.process: RenderProcess | None = None
        self.tracker: RenderTracker | None = None
        self.job: RenderJob | None = None
        self.caps: Capabilities | None = None
        self.settings: AppSettings | None = None
        self.project: ProjectInfo | None = None
        self.tmp_dir: Path | None = None
        self.plan: RenderPlan | None = None
        self.plans: list[RenderPlan] = []
        self.chunks: list[list[int]] = []
        self.chunk_index = -1
        self.status: str | None = None
        self.message = ""
        self.exit_code: int | None = None
        self.paused_frames: list[int] = []
        self.skipped_existing: list[int] = []
        self.extra_overrides: dict[str, Any] = {}
        self.retry_notes: list[str] = []
        self.oom_attempt = 0
        self.crash_attempt = 0
        self.started_at: datetime | None = None
        self._errors_before_chunk = 0
        self._pause_requested = False
        self._stop_requested = False

    # --- управление ---------------------------------------------------------------

    def is_running(self) -> bool:
        return self.process is not None and self.process.is_running()

    def is_paused(self) -> bool:
        return self.status == RUN_PAUSED and bool(self.paused_frames)

    def start(
        self,
        job: RenderJob,
        caps: Capabilities,
        settings: AppSettings,
        project: ProjectInfo,
        *,
        tmp_dir: Path | str,
    ) -> None:
        """Строит план, сканирует вывод при resume, запускает первую пачку. ValueError — задача противоречива."""
        if self.is_running():
            raise RuntimeError("A render is already running")
        base = self._plan_builder(job, caps, settings, project, tmp_dir=tmp_dir)
        frames = list(base.frames)
        self.job, self.caps, self.settings, self.project = job, caps, settings, project
        self.tmp_dir = Path(tmp_dir)
        self.plans = []
        self.plan = base
        self.status = None
        self.message = ""
        self.exit_code = None
        self.paused_frames = []
        self.extra_overrides = {}
        self.retry_notes = []
        self.oom_attempt = 0
        self.crash_attempt = 0
        self._pause_requested = False
        self._stop_requested = False
        self.started_at = datetime.now()

        self.skipped_existing = []
        if job.resume:
            extension = extension_for_format(job.file_format)
            scan = scan_output(base.output_path, frames, extensions=[extension] if extension else None)
            todo = scan.missing(frames, min_size_bytes=job.min_frame_kb * 1024)
            self.skipped_existing = [f for f in frames if f not in todo]
            frames = todo
        self.tracker = RenderTracker(frames)
        self.chunks = split_chunks(frames, job.chunk_size)
        self.chunk_index = 0
        self.started.emit()
        if self.skipped_existing:
            self.line_received.emit(
                f"[BRM] resume: {len(self.skipped_existing)} frame(s) already on disk, {len(frames)} to render"
            )
        if not frames:
            self._finish(RUN_SUCCESS, f"All {len(self.skipped_existing)} frame(s) are already rendered")
            return
        self._run_chunk(self.chunks[0])

    def pause(self) -> None:
        """Пауза после текущего кадра: процесс убивается на событии Time:."""
        if self.is_running():
            self._pause_requested = True

    def resume(self) -> None:
        if not self.is_paused() or self.is_running():
            return
        frames = list(self.paused_frames)
        self.paused_frames = []
        self.status = None
        self._pause_requested = False
        self._stop_requested = False
        assert self.job is not None
        self.chunks = split_chunks(frames, self.job.chunk_size)
        self.chunk_index = 0
        self.crash_attempt = 0
        self.line_received.emit(f"[BRM] resume: {len(frames)} frame(s) left")
        self._run_chunk(self.chunks[0])

    def stop(self) -> None:
        if self.is_running():
            self._pause_requested = False
            self._stop_requested = True
            assert self.process is not None
            self.process.stop()

    def frames_expected(self) -> int:
        return self.tracker.progress.frames_total if self.tracker else 0

    # --- пачки -------------------------------------------------------------------------

    def _run_chunk(self, frames: list[int]) -> None:
        assert self.job is not None and self.caps is not None and self.settings is not None
        assert self.project is not None and self.tmp_dir is not None
        assert self.tracker is not None
        job = self.job.model_copy(update={"overrides": {**self.job.overrides, **self.extra_overrides}})
        plan = self._plan_builder(
            job, self.caps, self.settings, self.project, tmp_dir=self.tmp_dir, frames_override=frames
        )
        self.plan = plan
        self.plans.append(plan)
        # Ошибки копятся за всю задачу, а решение о ретрае касается только текущей
        # попытки: запоминаем границу, иначе один out of memory в начале заставит
        # лечить памятью любое следующее падение (и врать про причину).
        self._errors_before_chunk = len(self.tracker.progress.errors)
        self._release_process()
        process = self._process_factory()
        process.line_received.connect(self._on_line)
        process.finished.connect(self._on_process_finished)
        self.process = process
        if len(self.chunks) > 1:
            self.line_received.emit(f"[BRM] chunk {self.chunk_index + 1}/{len(self.chunks)}: {describe_chunk(frames)}")
        self.chunk_started.emit(plan)
        process.start(plan)

    def _release_process(self) -> None:
        """Отпускает прошлый процесс: без этого они копятся детьми оркестратора всю сессию."""
        previous = self.process
        if previous is None:
            return
        try:
            previous.line_received.disconnect(self._on_line)
            previous.finished.disconnect(self._on_process_finished)
        except (RuntimeError, TypeError):  # уже отсоединён или удалён
            pass
        previous.deleteLater()
        self.process = None

    def _chunk_had_out_of_memory(self) -> bool:
        """Нехватка памяти именно в текущей попытке, а не когда-либо за задачу."""
        assert self.tracker is not None
        return any(is_out_of_memory(line) for line in self.tracker.progress.errors[self._errors_before_chunk :])

    def _on_line(self, line: str) -> None:
        self.line_received.emit(line)
        if self.tracker is None:
            return
        event: LogEvent = self.tracker.feed(line)
        if event.kind == KIND_TIME and self._pause_requested and self.is_running():
            assert self.process is not None
            self.process.stop(force=True)
        self.event_received.emit(event)

    def _current_chunk(self) -> list[int]:
        if 0 <= self.chunk_index < len(self.chunks):
            return self.chunks[self.chunk_index]
        return []

    def _remaining_overall(self) -> list[int]:
        """Кадры задачи, которых ещё нет. Считаем по всем пачкам, а не с текущей:
        так проверка в конце остаётся живой, а не всегда пустой."""
        assert self.tracker is not None
        done = set(self.tracker.progress.frames_done)
        return [frame for chunk in self.chunks for frame in chunk if frame not in done]

    def _on_process_finished(self, exit_code: int, status: str) -> None:
        assert self.tracker is not None
        self.exit_code = exit_code
        done = set(self.tracker.progress.frames_done)
        remaining_in_chunk = [f for f in self._current_chunk() if f not in done]

        if status == STATUS_STOPPED:
            remaining = self._remaining_overall()
            if self._pause_requested and remaining:
                self.paused_frames = remaining
                self._finish(RUN_PAUSED, f"Paused, {len(remaining)} frame(s) left")
            elif not remaining:
                self._finish(RUN_SUCCESS, "All frames rendered")
            else:
                self._finish(RUN_STOPPED, f"Stopped by user, {len(remaining)} frame(s) left")
            return

        if not remaining_in_chunk:
            self.crash_attempt = 0
            self._next_chunk_or_finish()
            return

        # Пачка не дошла до конца: out of memory → урезаем и повторяем, иначе один повтор.
        if self._chunk_had_out_of_memory():
            self.oom_attempt += 1
            step = oom_retry_overrides(self.oom_attempt)
            if step is not None:
                note, overrides = step
                self.extra_overrides.update(overrides)
                self.retry_notes.append(note)
                self.line_received.emit(f"[BRM] retry after out of memory: {note}")
                self._run_chunk(remaining_in_chunk)
                return
            self._finish(RUN_FAILED, "Out of memory even with the lightest settings")
            return
        if self.crash_attempt < MAX_CRASH_RETRIES:
            self.crash_attempt += 1
            self.line_received.emit(
                f"[BRM] retry {self.crash_attempt}/{MAX_CRASH_RETRIES} after {status} "
                f"(exit code {exit_code}): {describe_chunk(remaining_in_chunk)}"
            )
            self._run_chunk(remaining_in_chunk)
            return
        self._finish(RUN_FAILED, f"Blender {status} with exit code {exit_code}")

    def _next_chunk_or_finish(self) -> None:
        self.chunk_index += 1
        if self.chunk_index < len(self.chunks):
            self._run_chunk(self.chunks[self.chunk_index])
            return
        remaining = self._remaining_overall()
        if remaining:
            self._finish(RUN_FAILED, f"{len(remaining)} frame(s) were not rendered")
        else:
            self._finish(RUN_SUCCESS, "All frames rendered")

    def _finish(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        self._write_stats()
        self.finished.emit(status)

    def _write_stats(self) -> None:
        """Статистика всей задачи рядом с логом первой пачки: данные для графика и истории."""
        if self.tracker is None or not self.plans or self.job is None or self.started_at is None:
            return
        elapsed = (datetime.now() - self.started_at).total_seconds()
        first = self.plans[0]
        data = stats_dict(
            self.tracker.progress,
            status=self.status or "",
            exit_code=self.exit_code,
            duration_s=elapsed,
            extra={
                "blend_path": self.job.blend_path,
                "scene": first.scene.name if first.scene else self.job.scene,
                "preset": self.job.preset,
                "chunks": len(self.chunks),
                "retries": self.retry_notes,
                "skipped_existing": self.skipped_existing,
                "log_files": [plan.log_path.name for plan in self.plans],
                "output_path": first.output_path,
                # Для истории (M7): started_at не совпадает с finished_at при chunking/resume.
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        try:
            write_stats(first.stats_path, data)
        except OSError as exc:
            log.warning("Cannot write render stats: %s", exc)
