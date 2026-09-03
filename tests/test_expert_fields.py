"""Тесты core/expert_fields.py на фикстуре capabilities Blender 5.0.1."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.capabilities import Capabilities
from brm.core.expert_fields import (
    COMMON_GROUPS,
    EXCLUDED_GROUPS,
    FieldSpec,
    groups_for_engine,
    list_fields,
)


@pytest.fixture
def caps(fixtures_dir: Path) -> Capabilities:
    data = json.loads((fixtures_dir / "capabilities_blender_5.0.1.json").read_text(encoding="utf-8"))
    return Capabilities.model_validate(data)


def test_groups_for_engine() -> None:
    assert groups_for_engine("CYCLES") == ("cycles", "view_layer_cycles", *COMMON_GROUPS)
    assert groups_for_engine("BLENDER_EEVEE") == ("eevee", "eevee_ray_tracing", *COMMON_GROUPS)
    assert groups_for_engine("BLENDER_EEVEE_NEXT") == ("eevee", "eevee_ray_tracing", *COMMON_GROUPS)
    assert groups_for_engine("BLENDER_WORKBENCH") == COMMON_GROUPS
    assert groups_for_engine(None) == COMMON_GROUPS


def test_list_fields_empty_without_caps_or_engine(caps: Capabilities) -> None:
    assert list_fields(None, "CYCLES") == []
    assert list_fields(caps, None) == []


def test_cycles_fields_cover_known_properties(caps: Capabilities) -> None:
    fields = list_fields(caps, "CYCLES")
    paths = {f.path for f in fields}
    assert "cycles.samples" in paths
    assert "cycles.max_bounces" in paths
    assert "view_layer.cycles.denoising_store_passes" in paths
    assert "render.use_persistent_data" in paths
    assert "render.image_settings.file_format" in paths
    assert "view_settings.view_transform" in paths
    assert not any(p.startswith("eevee.") for p in paths)
    assert len(fields) > 150  # реально огромная форма, как и требует спека


def test_eevee_fields_use_eevee_groups_not_cycles(caps: Capabilities) -> None:
    fields = list_fields(caps, "BLENDER_EEVEE")
    paths = {f.path for f in fields}
    assert "eevee.taa_render_samples" in paths
    assert "eevee.ray_tracing_options.resolution_scale" in paths
    assert not any(p.startswith("cycles.") for p in paths)
    assert "render.use_persistent_data" in paths  # общее остаётся


def test_excluded_groups_never_appear(caps: Capabilities) -> None:
    for engine in ("CYCLES", "BLENDER_EEVEE"):
        fields = list_fields(caps, engine)
        assert not any(f.group in EXCLUDED_GROUPS for f in fields)
    assert EXCLUDED_GROUPS == ("cycles_preferences", "ffmpeg")


def test_fields_are_only_simple_settable_types(caps: Capabilities) -> None:
    for field in list_fields(caps, "CYCLES"):
        assert field.info.type in ("BOOLEAN", "INT", "FLOAT", "ENUM")
        assert not field.info.is_readonly
        assert not field.info.is_array
        if field.info.type == "ENUM":
            assert field.info.enum_items


def test_fields_sorted_alphabetically_within_section(caps: Capabilities) -> None:
    fields = list_fields(caps, "CYCLES")
    cycles_identifiers = [f.path.split(".")[-1] for f in fields if f.group == "cycles"]
    assert cycles_identifiers == sorted(cycles_identifiers)


def test_section_titles_are_human_readable(caps: Capabilities) -> None:
    fields = list_fields(caps, "CYCLES")
    sections = {f.section for f in fields}
    assert "Cycles" in sections
    assert "Output" in sections
    assert "Color Management" in sections
    assert not any(s.islower() and "_" in s for s in sections)  # не сырые имена групп


def test_field_matches_search() -> None:
    from brm.core.capabilities import PropertyInfo

    spec = FieldSpec(
        group="cycles",
        section="Cycles",
        path="cycles.max_bounces",
        info=PropertyInfo(identifier="max_bounces", type="INT", name="Max Bounces"),
    )
    assert spec.matches("")
    assert spec.matches("bounce")
    assert spec.matches("MAX_BOUNCES")
    assert spec.matches("cycles.max")
    assert not spec.matches("samples")


def test_view_settings_group_reachable_for_override(caps: Capabilities) -> None:
    """Путь без 'scene.' — override_template.py должен уметь его разрешить (см. roots).

    view_transform — OCIO-зависимый динамический enum: статически RNA отдаёт один
    служебный элемент-заглушку ("NONE"), реальный список приходит только через
    пробное присваивание (см. probe_caps.DYNAMIC_ENUM_CANDIDATES).
    """
    fields = list_fields(caps, "CYCLES")
    view_transform = next(f for f in fields if f.path == "view_settings.view_transform")
    assert view_transform.info.type == "ENUM"
    assert view_transform.info.enum_dynamic
    assert {"AgX", "Standard", "Filmic"} <= set(view_transform.info.enum_identifiers())


def test_render_engine_is_excluded(caps: Capabilities) -> None:
    """Движок выставляется пресетом/проектом, не отдельным полем формы (раздел 4.3 спеки)."""
    assert not any(f.path == "render.engine" for f in list_fields(caps, "CYCLES"))
    assert not any(f.path == "render.engine" for f in list_fields(caps, "BLENDER_EEVEE"))
