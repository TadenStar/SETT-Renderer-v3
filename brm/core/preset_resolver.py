"""Пресет + capabilities → список присваиваний для override-скрипта.

Свойства, которых нет в этой версии Blender, отбрасываются с причиной:
пользователь видит это в логе, а override не пытается присвоить заведомо
невозможное. Значение ``{"prefer": [...]}`` — список кандидатов enum: для
показа в форме берётся первый, который есть в статическом списке capabilities,
а в override уходит весь список, и ``safe_set_prefer`` внутри Blender выбирает
первый принятый. Так переживаются переименования вроде OPEN_EXR_MULTILAYER → OPEN_EXR
в 5.0, которые статический список RNA не показывает. Чистые функции без Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brm.core.capabilities import Capabilities, PropertyInfo, version_str
from brm.core.presets import EEVEE_ALIAS, Preset

PREFER_KEY = "prefer"
FILE_FORMAT_PATH = "render.image_settings.file_format"
EXR_FORMATS = ("OPEN_EXR", "OPEN_EXR_MULTILAYER")

# Порядок важен: более длинные префиксы первыми.
GROUP_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("render.image_settings.", "image_settings"),
    ("render.ffmpeg.", "ffmpeg"),
    ("view_settings.", "view_settings"),
    ("eevee.ray_tracing_options.", "eevee_ray_tracing"),
    ("view_layer.cycles.", "view_layer_cycles"),
    ("cycles.", "cycles"),
    ("eevee.", "eevee"),
    ("render.", "render"),
)


@dataclass
class SkippedSetting:
    path: str
    value: Any
    reason: str


@dataclass
class ResolvedPreset:
    preset: Preset
    engine: str
    file_format: str | None
    # Что уходит в override: строки, числа, булевы или {"prefer": [...]}.
    assignments: list[tuple[str, Any]] = field(default_factory=list)
    # Что показывать в форме: для prefer — первый статически доступный кандидат.
    display: dict[str, Any] = field(default_factory=dict)
    skipped: list[SkippedSetting] = field(default_factory=list)

    def value(self, path: str) -> Any:
        return self.display.get(normalize_path(path))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.assignments)


def normalize_path(path: str) -> str:
    return path[len("scene.") :] if path.startswith("scene.") else path


def group_for_path(path: str) -> tuple[str | None, str]:
    """('cycles', 'samples') для 'scene.cycles.samples'; (None, path) для неизвестного корня."""
    normalized = normalize_path(path)
    for prefix, group in GROUP_BY_PREFIX:
        if normalized.startswith(prefix):
            return group, normalized[len(prefix) :]
    return None, normalized


def is_prefer(value: Any) -> bool:
    return isinstance(value, dict) and PREFER_KEY in value


def resolve_engine(preset: Preset, caps: Capabilities, scene_engine: str) -> str:
    """Движок пресета или файла; псевдоним EEVEE → реальный идентификатор этой версии."""
    engine = preset.engine or scene_engine
    if engine == EEVEE_ALIAS:
        engine = caps.eevee_engine_id or "BLENDER_EEVEE"
    return engine


def _resolve_value(prop: PropertyInfo | None, value: Any) -> tuple[Any, Any, str | None]:
    """(значение для override, значение для показа, причина пропуска)."""
    if is_prefer(value):
        candidates = [str(c) for c in value[PREFER_KEY]]
        if not candidates:
            return None, None, "empty prefer list"
        if prop is None or not prop.enum_items:
            # Список неизвестен — пусть safe_set_prefer разбирается внутри Blender.
            return {PREFER_KEY: candidates}, candidates[0], None
        available = prop.enum_identifiers()
        for index, candidate in enumerate(candidates):
            if candidate in available:
                # В override уходит хвост списка начиная с первого доступного: запасные варианты остаются.
                return {PREFER_KEY: candidates[index:]}, candidate, None
        return None, None, f"none of {candidates} is available, options: {available}"
    if prop is not None and prop.type == "ENUM" and prop.enum_items and value not in prop.enum_identifiers():
        return None, None, f"value {value!r} is not available, options: {prop.enum_identifiers()}"
    return value, value, None


def _output_items(preset: Preset, display_format: str | None) -> list[tuple[str, Any]]:
    """Настройки вывода в правильном порядке: формат раньше глубины цвета и кодека."""
    out = preset.output
    items: list[tuple[str, Any]] = []
    if out.file_format is not None:
        items.append((FILE_FORMAT_PATH, out.file_format))
    if out.color_mode:
        items.append(("render.image_settings.color_mode", out.color_mode))
    if out.color_depth:
        items.append(("render.image_settings.color_depth", out.color_depth))
    if display_format == "PNG" and out.compression is not None:
        items.append(("render.image_settings.compression", out.compression))
    if display_format == "JPEG" and out.quality is not None:
        items.append(("render.image_settings.quality", out.quality))
    if display_format in EXR_FORMATS and out.exr_codec:
        items.append(("render.image_settings.exr_codec", out.exr_codec))
    if out.resolution_percentage is not None:
        items.append(("render.resolution_percentage", out.resolution_percentage))
    if out.resolution_x is not None:
        items.append(("render.resolution_x", out.resolution_x))
    if out.resolution_y is not None:
        items.append(("render.resolution_y", out.resolution_y))
    if out.fps is not None:
        items.append(("render.fps", out.fps))
    return items


def resolve_preset(preset: Preset, caps: Capabilities, scene_engine: str) -> ResolvedPreset:
    engine = resolve_engine(preset, caps, scene_engine)

    format_prop = caps.property("image_settings", "file_format")
    _, display_format, format_reason = _resolve_value(format_prop, preset.output.file_format)
    resolved = ResolvedPreset(preset=preset, engine=engine, file_format=display_format)

    sections: list[list[tuple[str, Any]]] = [_output_items(preset, display_format), list(preset.common.items())]
    if engine == "CYCLES":
        sections.append(list(preset.cycles.items()))
    elif engine.startswith("BLENDER_EEVEE"):
        sections.append(list(preset.eevee.items()))
    sections.append(list(preset.view_layer.items()))

    for section in sections:
        for path, raw_value in section:
            normalized = normalize_path(path)
            group, identifier = group_for_path(path)
            prop = caps.property(group, identifier) if group else None
            if group and group in caps.groups and prop is None:
                resolved.skipped.append(
                    SkippedSetting(normalized, raw_value, f"not available in Blender {version_str(caps.blender_version)}")
                )
                continue
            runtime_value, display_value, reason = _resolve_value(prop, raw_value)
            if reason is not None:
                resolved.skipped.append(SkippedSetting(normalized, raw_value, reason))
                continue
            resolved.assignments.append((normalized, runtime_value))
            resolved.display[normalized] = display_value
    if format_reason is not None and preset.output.file_format is not None:
        resolved.file_format = None
    return resolved


def compose_overrides(
    resolved: ResolvedPreset,
    custom: dict[str, Any] | None = None,
    untouched: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Итоговые присваивания: пресет, поверх — свои значения, минус «не трогать»."""
    result: dict[str, Any] = dict(resolved.assignments)
    for path, value in (custom or {}).items():
        result[normalize_path(path)] = value
    for path in untouched or ():
        result.pop(normalize_path(path), None)
    return result


def display_file_format(overrides: dict[str, Any], fallback: str | None) -> str | None:
    """Формат для ``-F``: строка, первый кандидат prefer или None — «из сцены»."""
    value = overrides.get(FILE_FORMAT_PATH)
    if is_prefer(value):
        candidates = value[PREFER_KEY]
        return str(candidates[0]) if candidates else fallback
    return str(value) if value else fallback
