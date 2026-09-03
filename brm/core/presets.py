"""Пресеты рендера (раздел 4.3 спеки). Значения — ``docs/02_RENDER_SETTINGS.md``.

Встроенные лежат в ``brm/resources/presets/*.json``, пользовательские —
в ``%APPDATA%/BRM/presets``; одноимённый пользовательский пресет замещает
встроенный. Пути свойств — как в override-скрипте: ``cycles.samples``,
``render.use_persistent_data``, ``eevee.taa_render_samples``,
``view_layer.cycles.denoising_store_passes``. Значение ``{"prefer": [...]}`` —
список кандидатов enum, из которого резолвер берёт первый доступный в этой версии.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brm.core.app_paths import package_root
from brm.core.storage import app_data_dir

log = logging.getLogger(__name__)

BUILTIN_PRESETS_DIR = package_root() / "brm" / "resources" / "presets"
EEVEE_ALIAS = "EEVEE"


class OutputSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Строка или {"prefer": [...]}: в 5.x OPEN_EXR_MULTILAYER исчез, EXR многослойный сам.
    file_format: str | dict[str, Any] | None = None
    color_mode: str | None = None
    color_depth: str | None = None
    compression: int | None = None
    quality: int | None = None
    exr_codec: str | None = None
    resolution_percentage: int | None = None
    resolution_x: int | None = None
    resolution_y: int | None = None
    fps: int | None = None


class Preset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    name: str
    order: int = 100
    description: str = ""
    # None — движок как в файле; "CYCLES" или "EEVEE" (псевдоним для BLENDER_EEVEE*).
    engine: str | None = None
    output: OutputSettings = Field(default_factory=OutputSettings)
    common: dict[str, Any] = Field(default_factory=dict)
    cycles: dict[str, Any] = Field(default_factory=dict)
    eevee: dict[str, Any] = Field(default_factory=dict)
    view_layer: dict[str, Any] = Field(default_factory=dict)
    chunk_size: int | None = None
    video: dict[str, Any] = Field(default_factory=dict)
    # Заполняется загрузчиком.
    builtin: bool = False
    source: str = ""


class PresetError(RuntimeError):
    """Файл пресета не читается или не проходит валидацию."""


def user_presets_dir() -> Path:
    return app_data_dir() / "presets"


def load_preset_file(path: str | os.PathLike[str], *, builtin: bool = False) -> Preset:
    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        preset = Preset.model_validate(data)
    except (OSError, ValueError, ValidationError) as exc:
        raise PresetError(f"Preset {file.name} is unreadable: {exc}") from exc
    preset.builtin = builtin
    preset.source = str(file)
    return preset


def load_presets(
    builtin_dir: str | os.PathLike[str] | None = None,
    user_dir: str | os.PathLike[str] | None = None,
    *,
    strict: bool = False,
) -> list[Preset]:
    """Встроенные плюс пользовательские, отсортированные по ``order`` и имени.

    Битый пользовательский файл пропускается с записью в лог (``strict`` — исключение).
    """
    by_name: dict[str, Preset] = {}
    sources: Iterable[tuple[Path, bool]] = (
        (Path(builtin_dir) if builtin_dir is not None else BUILTIN_PRESETS_DIR, True),
        (Path(user_dir) if user_dir is not None else user_presets_dir(), False),
    )
    for directory, builtin in sources:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.json")):
            try:
                preset = load_preset_file(file, builtin=builtin)
            except PresetError as exc:
                if strict or builtin:
                    raise
                log.warning("%s", exc)
                continue
            by_name[preset.name] = preset
    return sorted(by_name.values(), key=lambda p: (p.order, p.name.lower()))


def find_preset(presets: Iterable[Preset], name: str | None) -> Preset | None:
    for preset in presets:
        if preset.name == name:
            return preset
    return None
