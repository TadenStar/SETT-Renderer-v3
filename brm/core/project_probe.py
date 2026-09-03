"""Проба проекта (раздел 3.3 спеки): сцены, камеры, кадры, разрешение из .blend.

JSON пишет ``scripts/probe_scene.py`` внутри Blender, здесь он превращается в
``ProjectInfo`` и дополняется предупреждениями для пользователя.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brm.core.blender_process import describe_failure, run_blender, script_path
from brm.core.capabilities import version_str

log = logging.getLogger(__name__)

PROBE_SCRIPT = "probe_scene.py"


class ViewLayerInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    use: bool = True


class MarkerInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    frame: int
    camera: str | None = None


class SceneInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    engine: str = ""
    frame_start: int = 1
    frame_end: int = 1
    frame_step: int = 1
    frame_current: int = 1
    fps: float = 0.0
    resolution_x: int = 0
    resolution_y: int = 0
    resolution_percentage: int = 100
    output_path: str = ""
    file_format: str = ""
    color_mode: str = ""
    color_depth: str = ""
    active_camera: str | None = None
    cameras: list[str] = Field(default_factory=list)
    view_layers: list[ViewLayerInfo] = Field(default_factory=list)
    markers: list[MarkerInfo] = Field(default_factory=list)
    use_compositing: bool = False
    has_compositor_tree: bool = False
    use_sequencer: bool = False
    sequencer_strips: int = 0
    render_summary: dict[str, Any] = Field(default_factory=dict)

    @property
    def frame_count(self) -> int:
        if self.frame_end < self.frame_start:
            return 0
        return len(range(self.frame_start, self.frame_end + 1, max(self.frame_step, 1)))

    @property
    def final_resolution(self) -> tuple[int, int]:
        scale = self.resolution_percentage / 100.0
        return (round(self.resolution_x * scale), round(self.resolution_y * scale))


class ProjectInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    probe_version: int = 1
    file_path: str
    # Версия формата файла (major, minor, subversion) — из bpy.data.version.
    saved_with_version: tuple[int, int, int] = (0, 0, 0)
    # Релиз Blender, которым читали файл, и версия формата, которую он пишет.
    blender_version: tuple[int, int, int] = (0, 0, 0)
    blender_version_file: tuple[int, int, int] = (0, 0, 0)
    active_scene: str | None = None
    scenes: list[SceneInfo] = Field(default_factory=list)
    enabled_addons: list[str] = Field(default_factory=list)
    missing_libraries: list[str] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)

    def scene(self, name: str | None) -> SceneInfo | None:
        for scene in self.scenes:
            if scene.name == name:
                return scene
        return None

    def default_scene(self) -> SceneInfo | None:
        return self.scene(self.active_scene) or (self.scenes[0] if self.scenes else None)


class ProjectProbeError(RuntimeError):
    """Файл не прочитался: нет файла, Blender упал, JSON неразборчив."""


def file_version_str(version: tuple[int, int, int] | list[int]) -> str:
    """Версия формата файла для пользователя: только major.minor, subversion не показываем."""
    return ".".join(str(v) for v in tuple(version)[:2])


def project_warnings(info: ProjectInfo) -> list[str]:
    """Что пользователь должен увидеть до старта рендера (разделы 4.2, 6 и 7 спеки)."""
    warnings: list[str] = []
    saved, current = tuple(info.saved_with_version), tuple(info.blender_version_file)
    if current != (0, 0, 0) and saved > current:
        warnings.append(
            f"File was saved with Blender {file_version_str(saved)} (file format "
            f"{version_str(saved)}), newer than the selected Blender "
            f"{version_str(info.blender_version)}"
        )
    for scene in info.scenes:
        if scene.file_format == "FFMPEG":
            warnings.append(
                f"Scene '{scene.name}' outputs an FFMPEG video. Render an image sequence "
                "instead and assemble the video afterwards"
            )
        if scene.use_sequencer and scene.sequencer_strips > 0:
            warnings.append(
                f"Scene '{scene.name}' has {scene.sequencer_strips} sequencer strips and "
                "Use Sequencer is on: Blender would render the sequencer, not the 3D scene"
            )
        if scene.frame_end < scene.frame_start:
            warnings.append(
                f"Scene '{scene.name}' has an empty frame range "
                f"({scene.frame_start}..{scene.frame_end})"
            )
        if not scene.cameras:
            warnings.append(f"Scene '{scene.name}' has no camera")
    missing = list(dict.fromkeys(info.missing_libraries))
    if missing:
        listed = ", ".join(missing[:3])
        more = "" if len(missing) <= 3 else f" and {len(missing) - 3} more"
        warnings.append(f"Missing linked libraries: {listed}{more}")
    return warnings


def probe_project(
    blender_path: str | os.PathLike[str],
    blend_path: str | os.PathLike[str],
    *,
    tmp_dir: str | os.PathLike[str],
    timeout: float = 300.0,
    cancel: threading.Event | None = None,
) -> ProjectInfo:
    """Запускает probe_scene.py на файле и разбирает результат."""
    blend = Path(blend_path)
    if not blend.is_file():
        raise ProjectProbeError(f"File not found: {blend}")
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"_brm_scene_{uuid.uuid4().hex}.json"
    args = [
        "-b",
        blend,
        "--python-exit-code",
        "1",
        "--python",
        script_path(PROBE_SCRIPT),
        "--",
        out,
    ]
    result = run_blender(blender_path, args, timeout=timeout, cancel=cancel)
    try:
        if not result.ok:
            raise ProjectProbeError(describe_failure("Project probe", result))
        if not out.is_file():
            raise ProjectProbeError("Project probe produced no output file\n" + result.tail())
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
            info = ProjectInfo.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise ProjectProbeError(f"Project probe output is unreadable: {exc}") from exc
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    if not info.file_path:
        info.file_path = str(blend)
    return info
