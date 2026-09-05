"""Помощники для Qt-тестов: ожидание состояния и поддельный рендер вместо Blender."""
from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from brm.core.command_builder import command_line
from brm.core.render_plan import RenderPlan


# Запас на загруженную машину: во время настоящего рендера Cycles очередь
# событий Qt разбирается заметно медленнее, и пять секунд стали мигать.
def wait_until(qapp, predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    """Крутит цикл событий, пока условие не выполнится: сигналы из потоков и процессов идут через очередь."""
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        time.sleep(0.01)
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for the UI state")


def fake_render_script(frames: list[int], delay: float, failure: str | None = None, output_dir: str | None = None) -> str:
    """Скрипт для ``python -c``: строки в формате Blender 5.0 по кадру за раз.

    ``failure``: ``"oom"`` — падение по памяти до первого кадра, ``"crash"`` — код выхода 3.
    С ``output_dir`` пишет файлы кадров, как настоящий Blender.
    """
    lines = [
        "import sys, time, os",
        f"frames = {frames!r}",
        f"out = {output_dir!r}",
        "print('Rendering animation (frames %d..%d)' % (frames[0], frames[-1]), flush=True)",
    ]
    if failure == "oom":
        lines += ["print('CUDA error: Out of memory in cuMemAlloc', flush=True)", "sys.exit(1)"]
    elif failure == "crash":
        lines += ["print('Error: simulated crash', flush=True)", "sys.exit(3)"]
    lines += [
        "for f in frames:",
        "    print('00:00.100  render           | Rendering frame %d' % f, flush=True)",
        "    print('00:00.100  render           | Fra: %d | Mem: 10M | Sample 8/16' % f, flush=True)",
        f"    time.sleep({delay})",
        "    if out:",
        "        os.makedirs(out, exist_ok=True)",
        "        open(os.path.join(out, '%04d.png' % f), 'wb').write(b'PNG' * 100)",
        "    print(\"00:00.200  render           | Saved: '%s'\" % os.path.join(out or 'D:/out', '%04d.png' % f), flush=True)",
        "    print('00:00.200  render           | Time: 00:00.10 (Saving: 00:00.00)', flush=True)",
        "print('Blender quit', flush=True)",
    ]
    return "\n".join(lines) + "\n"


class FakePlanBuilder:
    """Подмена build_render_plan: вместо Blender запускается python со скриптом выше.

    ``failures`` — номер запуска пачки (с 1) → ``"oom"`` или ``"crash"``.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        base_frames: list[int] | None = None,
        delay: float = 0.05,
        failures: dict[int, str] | None = None,
        write_files: bool = True,
    ) -> None:
        self.tmp_path = tmp_path
        self.base_frames = base_frames or [1, 2, 3]
        self.delay = delay
        self.failures = failures or {}
        self.write_files = write_files
        self.jobs: list = []
        self.chunk_calls: list[list[int]] = []

    @property
    def output_dir(self) -> Path:
        return self.tmp_path / "out"

    def __call__(self, job, caps, settings, project, *, tmp_dir, frames_override=None) -> RenderPlan:
        self.jobs.append(job)
        frames = list(frames_override) if frames_override is not None else list(self.base_frames)
        if frames_override is not None:
            self.chunk_calls.append(frames)
            call_no = len(self.chunk_calls)
            script = fake_render_script(
                frames, self.delay, self.failures.get(call_no), str(self.output_dir) if self.write_files else None
            )
            argv = [sys.executable, "-c", script]
        else:
            argv = [sys.executable, "-c", "print('base plan')"]
        out = self.output_dir
        return RenderPlan(
            job=job,
            argv=argv,
            command_line=command_line(argv),
            override_script=self.tmp_path / "override.py",
            override_settings={},
            output_path=str(out / "####"),
            output_dir=out,
            frames=frames,
            engine=job.engine or "CYCLES",
            cycles_device=None,
            log_path=out / f"render_log_{len(self.jobs)}.txt",
            scene=project.default_scene() if project is not None else None,
        )
