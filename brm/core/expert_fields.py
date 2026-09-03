"""Список полей для экспертной формы (раздел 4.3 спеки): вся форма из capabilities.

Каждое поле — путь для override (как в assignments пресета, без ведущего
``scene.``) плюс ``PropertyInfo`` из пробы, откуда берутся тип, границы и
варианты enum. Путь строится из ``PropertyGroup.path`` (реальный путь RNA,
который пишет ``probe_caps.py``), а не подбирается вручную — так он не
разъедется с capabilities при следующей версии Blender.

Что не показываем и почему:
- строки, массивы, read-only — их нельзя безопасно переопределить через
  простое присваивание (раздел 3.1 спеки: safe_set только для простых типов);
- ``cycles_preferences`` — настройки устройства общесистемные, не для одного
  рендера (место — Settings, см. ``capabilities.best_cycles_device()``);
- ``ffmpeg`` (вывод Blender в видео) — спека запрещает рендерить сразу в
  видеофайл, секвенция и сборка ffmpeg (M6) идут отдельно.

Чистые функции без Qt.
"""
from __future__ import annotations

from dataclasses import dataclass

from brm.core.capabilities import Capabilities, PropertyInfo
from brm.core.preset_resolver import normalize_path

SUPPORTED_TYPES = ("BOOLEAN", "INT", "FLOAT", "ENUM")
EXCLUDED_GROUPS = ("cycles_preferences", "ffmpeg")
# Движок сцены выставляется пресетом/проектом, а не отдельным полем формы:
# статический список RNA для render.engine показывает только текущий движок
# (не все доступные), редактировать его здесь означало бы врать про варианты.
EXCLUDED_FIELDS = ("render.engine",)

COMMON_GROUPS: tuple[str, ...] = ("render", "image_settings", "view_settings")

SECTION_TITLES: dict[str, str] = {
    "cycles": "Cycles",
    "eevee": "EEVEE",
    "eevee_ray_tracing": "EEVEE — Ray Tracing",
    "view_layer_cycles": "View Layer (Cycles)",
    "render": "Render",
    "image_settings": "Output",
    "view_settings": "Color Management",
}


@dataclass(frozen=True)
class FieldSpec:
    group: str
    section: str
    path: str  # без "scene." — как в словаре overrides
    info: PropertyInfo

    @property
    def label(self) -> str:
        return self.info.name or self.info.identifier

    def matches(self, query: str) -> bool:
        if not query:
            return True
        query = query.lower()
        return query in self.info.identifier.lower() or query in self.label.lower() or query in self.path.lower()


def groups_for_engine(engine: str | None) -> tuple[str, ...]:
    """Группы capabilities для этого движка, в порядке показа: движок, потом общее."""
    specific: tuple[str, ...] = ()
    if engine == "CYCLES":
        specific = ("cycles", "view_layer_cycles")
    elif engine and engine.startswith("BLENDER_EEVEE"):
        specific = ("eevee", "eevee_ray_tracing")
    return (*specific, *COMMON_GROUPS)


def _is_usable(info: PropertyInfo) -> bool:
    if info.type not in SUPPORTED_TYPES or info.is_readonly or info.is_array:
        return False
    return info.type != "ENUM" or bool(info.enum_items)


def list_fields(caps: Capabilities | None, engine: str | None) -> list[FieldSpec]:
    """Все переопределяемые свойства этого движка, сгруппированные и упорядоченные."""
    if caps is None or engine is None:
        return []
    fields: list[FieldSpec] = []
    for group in groups_for_engine(engine):
        if group in EXCLUDED_GROUPS:
            continue
        prop_group = caps.groups.get(group)
        if prop_group is None:
            continue
        prefix = normalize_path(prop_group.path) if prop_group.path else group
        section = SECTION_TITLES.get(group, group)
        for identifier in sorted(prop_group.properties):
            info = prop_group.properties[identifier]
            path = f"{prefix}.{identifier}"
            if not _is_usable(info) or path in EXCLUDED_FIELDS:
                continue
            fields.append(FieldSpec(group=group, section=section, path=path, info=info))
    return fields
