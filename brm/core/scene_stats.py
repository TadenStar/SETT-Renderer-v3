"""Анализ сцены по кнопке: объекты, геометрия, инстансы.

Отдельная проба, а не часть ``probe_project``: подсчёт треугольников считает
depsgraph на render-уровне, и на тяжёлой сцене это заметно долго. Открытие
проекта из-за анализа тормозить не должно, поэтому он запускается по запросу.

Инстансы полезно видеть отдельно от объектов: тысяча инстансов одного меша
и тысяча копий — это одинаковое число объектов, но совершенно разная память.
Без Qt.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from brm.core.blender_process import describe_failure, run_blender, script_path

PROBE_SCRIPT = "probe_stats.py"


class SceneStats(BaseModel):
    """Что показал анализ. Неизвестные ключи игнорируются, как в остальных моделях."""

    model_config = ConfigDict(extra="ignore")

    probe_version: int = 1
    scene: str = ""
    objects: int = 0
    evaluated_objects: int = 0
    instances: int = 0
    meshes: int = 0
    triangles: int = 0
    instanced_triangles: int = 0
    objects_by_type: dict[str, int] = {}
    camera_culled_objects: int = 0
    log: list[str] = []

    @property
    def instanced_share(self) -> float:
        """Доля геометрии, пришедшей из инстансов: 0.0 — инстансов нет."""
        return self.instanced_triangles / self.triangles if self.triangles else 0.0

    def summary(self) -> str:
        """Одна строка для окна анализа. На английском, как весь интерфейс."""
        parts = [
            f"{self.objects} objects",
            f"{format_count(self.triangles)} triangles",
        ]
        if self.instances:
            parts.append(f"{self.instances} instances ({self.instanced_share:.0%} of the geometry)")
        else:
            parts.append("no instances")
        if self.camera_culled_objects:
            parts.append(f"{self.camera_culled_objects} objects set to camera cull")
        return " · ".join(parts)


class SceneStatsError(RuntimeError):
    pass


def format_count(value: int) -> str:
    """1234567 → «1.2 M»: точное число треугольников читать неудобно."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} M"
    if value >= 1_000:
        return f"{value / 1_000:.1f} k"
    return str(value)


def analyze_scene(
    blender_path: str | os.PathLike[str],
    blend_path: str | os.PathLike[str],
    *,
    scene: str = "",
    tmp_dir: str | os.PathLike[str],
    timeout: float = 600.0,
    cancel: threading.Event | None = None,
) -> SceneStats:
    """Запускает probe_stats.py на файле и разбирает результат."""
    blend = Path(blend_path)
    if not blend.is_file():
        raise SceneStatsError(f"File not found: {blend}")
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / f"_brm_stats_{uuid.uuid4().hex}.json"
    args = ["-b", blend, "--python-exit-code", "1", "--python", script_path(PROBE_SCRIPT), "--", out, scene]
    result = run_blender(blender_path, args, timeout=timeout, cancel=cancel)
    try:
        if not result.ok:
            raise SceneStatsError(describe_failure("Scene analysis", result))
        if not out.is_file():
            raise SceneStatsError("Scene analysis produced no output file\n" + result.tail())
        try:
            return SceneStats.model_validate(json.loads(out.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            raise SceneStatsError(f"Scene analysis output is unreadable: {exc}") from exc
    finally:
        try:
            out.unlink()
        except OSError:
            pass
