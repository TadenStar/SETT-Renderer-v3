"""Модель задачи рендера (раздел 4.5 спеки). Заложена с M1, команду из неё собирает M2."""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from brm.core.frame_range import FrameRange

# Шаблон вывода. Плейсхолдеры разворачивает приложение, «####» уходит в Blender как есть.
DEFAULT_OUTPUT_TEMPLATE = "{output_dir}/{project}/{scene}/####"
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_UNSAFE_RE = re.compile(r'[<>:"|?*\x00-\x1f]')


class RenderJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    blend_path: str
    scene: str | None = None
    view_layer: str | None = None
    frame_range: FrameRange = Field(default_factory=FrameRange)
    output_template: str = DEFAULT_OUTPUT_TEMPLATE
    preset: str | None = None
    # Движок и устройство: None — как в файле / лучшее доступное из capabilities.
    engine: str | None = None
    cycles_device: str | None = None
    # None — формат берётся из сцены (-F не передаётся). Строка только когда
    # пользователь или пресет выбрали формат осознанно.
    file_format: str | None = None
    threads: int | None = None
    factory_startup: bool = False
    # Присваивания RNA-свойств для override.py: «scene.cycles.samples» → 128.
    # Пресеты M4 раскладываются сюда же.
    overrides: dict[str, Any] = Field(default_factory=dict)
    # Защита от падений (M5): пропускать готовые кадры, перерендеривать кадры
    # меньше порога, бить задачу на пачки по N кадров (None — из пресета, 0 — выкл).
    resume: bool = True
    min_frame_kb: int = 0
    chunk_size: int | None = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def project_name(self) -> str:
        return Path(self.blend_path).stem


def safe_component(text: str) -> str:
    """Имя сцены или проекта как часть пути: убираем символы, запрещённые в Windows."""
    cleaned = _UNSAFE_RE.sub("_", text).strip(" .")
    return cleaned or "_"


def expand_output_template(
    template: str,
    *,
    output_dir: str = "",
    project: str = "",
    scene: str = "",
    preset: str | None = None,
) -> str:
    """Подставляет плейсхолдеры и нормализует слеши. Неизвестные плейсхолдеры остаются."""
    values = {
        "output_dir": output_dir.strip(),
        "project": safe_component(project),
        "scene": safe_component(scene),
        "preset": safe_component(preset or "default"),
    }

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1), match.group(0))

    expanded = _PLACEHOLDER_RE.sub(replace, template)
    return os.path.normpath(expanded)
