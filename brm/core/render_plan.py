"""План рендера: из задачи, capabilities и данных проекта — argv, override и пути.

Чистая функция без Qt. Раннер только выполняет готовый план, UI только
показывает его (панель «Command»).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from brm.core.capabilities import Capabilities
from brm.core.command_builder import build_argv, command_line
from brm.core.frame_range import resolve_frames
from brm.core.models import RenderJob, expand_output_template
from brm.core.override_builder import build_override_settings, write_override_script
from brm.core.project_probe import ProjectInfo, SceneInfo
from brm.core.storage import AppSettings


@dataclass
class RenderPlan:
    job: RenderJob
    argv: list[str]
    command_line: str
    override_script: Path
    override_settings: dict[str, Any]
    output_path: str
    output_dir: Path
    frames: list[int]
    engine: str
    cycles_device: str | None
    log_path: Path
    scene: SceneInfo | None = field(default=None, repr=False)


def log_file_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"render_log_{stamp}.txt"


def build_render_plan(
    job: RenderJob,
    caps: Capabilities,
    settings: AppSettings,
    project: ProjectInfo,
    *,
    tmp_dir: str | os.PathLike[str],
) -> RenderPlan:
    """Собирает всё для запуска. Бросает ValueError, если задача противоречива."""
    scene = project.scene(job.scene) or project.default_scene()
    if scene is None:
        raise ValueError("The project has no scenes")
    frames = resolve_frames(job.frame_range, scene_start=scene.frame_start, scene_end=scene.frame_end)

    output_base = settings.default_output_dir or str(Path(job.blend_path).parent)
    output_path = expand_output_template(
        job.output_template,
        output_dir=output_base,
        project=job.project_name,
        scene=scene.name,
        preset=job.preset,
    )
    output_dir = Path(output_path).parent

    engine = job.engine or scene.engine
    cycles_device = None
    if engine == "CYCLES":
        cycles_device = job.cycles_device or caps.best_cycles_device()

    override_settings = build_override_settings(
        job, scene_name=scene.name, engine=engine, compute_device_type=cycles_device
    )
    override_script = write_override_script(override_settings, tmp_dir, job.id)

    argv = build_argv(
        caps.blender_path,
        job.blend_path,
        frames=frames,
        output_path=output_path,
        scene=scene.name,
        override_script=override_script,
        file_format=job.file_format,
        threads=job.threads,
        factory_startup=job.factory_startup,
        cycles_device=cycles_device,
    )
    return RenderPlan(
        job=job,
        argv=argv,
        command_line=command_line(argv),
        override_script=override_script,
        override_settings=override_settings,
        output_path=output_path,
        output_dir=output_dir,
        frames=frames,
        engine=engine,
        cycles_device=cycles_device,
        log_path=output_dir / log_file_name(),
        scene=scene,
    )
