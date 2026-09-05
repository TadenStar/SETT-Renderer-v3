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
# Третья строка списка: место, где пользователь собирает свои настройки, чтобы
# потом сохранить их под именем. Значения живут в AppSettings.manual_overrides,
# а не в файле: файл появляется только когда пресету дали имя.
MANUAL_PRESET_NAME = "Manual"
# Между встроенными (10, 20) и именованными пользовательскими (200).
MANUAL_ORDER = 50
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
    # Доля от того, что стоит в сцене: 0.5 — «половина от вашей выдачи».
    # Абсолютный процент у Draft спорил бы с художником так же, как это делал
    # Final со своими 100% (этап 1): у Павла в сцене 50%, и Draft на 50%
    # не давал никакого выигрыша.
    resolution_scale: float | None = None
    resolution_x: int | None = None
    resolution_y: int | None = None
    fps: int | None = None


class Preset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    name: str
    order: int = 100
    description: str = ""
    # Показывается под описанием заметным цветом: у Super — что кадр небыстрый.
    warning: str = ""
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


# --- свои пресеты ------------------------------------------------------------------

# Пути вывода, которые в пресете живут в секции output, а не в common.
_OUTPUT_PATHS: dict[str, str] = {
    "render.image_settings.file_format": "file_format",
    "render.image_settings.color_mode": "color_mode",
    "render.image_settings.color_depth": "color_depth",
    "render.image_settings.compression": "compression",
    "render.image_settings.quality": "quality",
    "render.image_settings.exr_codec": "exr_codec",
    "render.resolution_percentage": "resolution_percentage",
    "render.resolution_x": "resolution_x",
    "render.resolution_y": "resolution_y",
    "render.fps": "fps",
}
_SECTION_PREFIXES: tuple[tuple[str, str], ...] = (
    ("view_layer.", "view_layer"),
    ("cycles.", "cycles"),
    ("eevee.", "eevee"),
)


def safe_preset_filename(name: str) -> str:
    """Имя файла из имени пресета: кириллица и пробелы допустимы, служебные символы — нет."""
    cleaned = "".join("_" if ch in r'<>:"/\|?*' or ord(ch) < 32 else ch for ch in name).strip(" .")
    return (cleaned or "preset") + ".json"


def preset_from_overrides(name: str, overrides: dict[str, Any], *, description: str = "") -> Preset:
    """Пресет из текущих значений формы: плоские пути раскладываются по секциям."""
    sections: dict[str, dict[str, Any]] = {"common": {}, "cycles": {}, "eevee": {}, "view_layer": {}}
    output: dict[str, Any] = {}
    for path, value in overrides.items():
        field = _OUTPUT_PATHS.get(path)
        if field is not None:
            output[field] = value
            continue
        for prefix, section in _SECTION_PREFIXES:
            if path.startswith(prefix):
                sections[section][path] = value
                break
        else:
            sections["common"][path] = value
    return Preset(
        name=name,
        order=200,  # свои пресеты идут после встроенных
        description=description,
        output=OutputSettings.model_validate(output),
        **sections,
    )


def manual_preset(overrides: dict[str, Any] | None = None) -> Preset:
    """Пресет «Manual» из текущих значений пользователя. Пустой — всё берётся из сцены."""
    preset = preset_from_overrides(
        MANUAL_PRESET_NAME,
        dict(overrides or {}),
        description="Your own settings. Tune them here, then Save as… to keep them under a name.",
    )
    return preset.model_copy(update={"order": MANUAL_ORDER})


def is_manual(preset: Preset | None) -> bool:
    return preset is not None and preset.name == MANUAL_PRESET_NAME


def save_user_preset(preset: Preset, user_dir: str | os.PathLike[str] | None = None) -> Path:
    """Пишет пресет в папку пользователя. Существующий с тем же именем перезаписывается."""
    directory = Path(user_dir) if user_dir is not None else user_presets_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_preset_filename(preset.name)
    payload = preset.model_dump(mode="json", exclude={"builtin", "source"}, exclude_defaults=True)
    payload["name"] = preset.name  # имя нужно всегда, даже если совпало с дефолтом
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as exc:
        raise PresetError(f"Could not save preset {preset.name}: {exc}") from exc
    return path


def delete_user_preset(name: str, user_dir: str | os.PathLike[str] | None = None) -> bool:
    """Удаляет свой пресет. False — файла не было. Встроенные не трогаются никогда."""
    directory = Path(user_dir) if user_dir is not None else user_presets_dir()
    path = directory / safe_preset_filename(name)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise PresetError(f"Could not delete preset {name}: {exc}") from exc
    return True
