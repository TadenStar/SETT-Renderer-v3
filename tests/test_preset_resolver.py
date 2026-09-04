"""Тесты core/preset_resolver.py на фикстуре capabilities Blender 5.0.1."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.capabilities import Capabilities
from brm.core.preset_resolver import (
    FILE_FORMAT_PATH,
    compose_overrides,
    display_file_format,
    group_for_path,
    resolve_engine,
    resolve_preset,
)
from brm.core.presets import OutputSettings, Preset, load_presets


@pytest.fixture
def caps(fixtures_dir: Path) -> Capabilities:
    data = json.loads((fixtures_dir / "capabilities_blender_5.0.1.json").read_text(encoding="utf-8"))
    return Capabilities.model_validate(data)


@pytest.fixture
def presets(tmp_path: Path) -> dict[str, Preset]:
    return {p.name: p for p in load_presets(user_dir=tmp_path / "none")}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("scene.cycles.samples", ("cycles", "samples")),
        ("cycles.samples", ("cycles", "samples")),
        ("render.image_settings.file_format", ("image_settings", "file_format")),
        ("eevee.ray_tracing_options.resolution_scale", ("eevee_ray_tracing", "resolution_scale")),
        ("view_layer.cycles.denoising_store_passes", ("view_layer_cycles", "denoising_store_passes")),
        ("render.use_persistent_data", ("render", "use_persistent_data")),
        ("view_layer.use_pass_cryptomatte_object", (None, "view_layer.use_pass_cryptomatte_object")),
    ],
)
def test_group_for_path(path: str, expected: tuple) -> None:
    assert group_for_path(path) == expected


def test_resolve_engine_alias(caps: Capabilities) -> None:
    assert resolve_engine(Preset(name="x"), caps, "BLENDER_EEVEE") == "BLENDER_EEVEE"
    assert resolve_engine(Preset(name="x", engine="EEVEE"), caps, "CYCLES") == "BLENDER_EEVEE"
    assert resolve_engine(Preset(name="x", engine="CYCLES"), caps, "BLENDER_EEVEE") == "CYCLES"


def test_balanced_for_cycles(caps: Capabilities, presets: dict[str, Preset]) -> None:
    resolved = resolve_preset(presets["Super"], caps, "CYCLES")
    assert resolved.engine == "CYCLES" and resolved.file_format is None  # формат из сцены
    values = resolved.as_dict()
    assert FILE_FORMAT_PATH not in values  # вывод остаётся таким, как настроен в .blend
    assert values["cycles.samples"] == 512
    # prefer: в форме — первый статически доступный, в override — хвост списка с него.
    assert resolved.value("cycles.sampling_pattern") == "BLUE_NOISE"
    assert values["cycles.sampling_pattern"] == {"prefer": ["BLUE_NOISE", "TABULATED_SOBOL", "SOBOL_BURLEY", "AUTOMATIC"]}
    assert resolved.value("cycles.denoiser") == "OPENIMAGEDENOISE"
    assert values["cycles.denoiser"] == {"prefer": ["OPENIMAGEDENOISE", "OPTIX"]}
    assert resolved.value("scene.cycles.samples") == 512
    assert values["render.use_persistent_data"] is True
    assert not [path for path in values if path.startswith("eevee.")]
    for skipped in resolved.skipped:
        assert skipped.reason
    unavailable = {s.path for s in resolved.skipped}
    assert "cycles.samples" not in unavailable


def test_balanced_for_eevee_uses_eevee_section(caps: Capabilities, presets: dict[str, Preset]) -> None:
    resolved = resolve_preset(presets["Super"], caps, "BLENDER_EEVEE")
    values = resolved.as_dict()
    assert values["eevee.taa_render_samples"] == 192
    assert values["eevee.ray_tracing_options.resolution_scale"] == "1"
    assert not [path for path in values if path.startswith("cycles.")]
    assert values["render.use_persistent_data"] is True


def test_output_sections_per_format(caps: Capabilities, presets: dict[str, Preset]) -> None:
    draft = resolve_preset(presets["Draft"], caps, "CYCLES").as_dict()
    assert draft[FILE_FORMAT_PATH] == "JPEG" and draft["render.image_settings.quality"] == 90
    assert "render.image_settings.compression" not in draft
    # Без процента сцены относительное разрешение просто не выставляется.
    assert "render.resolution_percentage" not in draft

    final_resolved = resolve_preset(presets["Super"], caps, "CYCLES")
    final = final_resolved.as_dict()
    # Super не навязывает формат: он и его параметры берутся из сцены.
    assert final_resolved.file_format is None
    assert FILE_FORMAT_PATH not in final
    assert not [key for key in final if key.startswith("render.image_settings.")]

    # Формат в argv: у Super его нет вовсе, у Draft — свой.
    assert display_file_format(final, None) is None
    assert display_file_format({FILE_FORMAT_PATH: "JPEG"}, None) == "JPEG"
    assert display_file_format({}, "PNG") == "PNG"

    draft = resolve_preset(presets["Draft"], caps, "CYCLES", scene_percentage=50).as_dict()
    assert draft[FILE_FORMAT_PATH] == "JPEG"
    # Draft берёт половину от процента сцены, а не абсолютное число.
    assert draft["render.resolution_percentage"] == 25


def test_unknown_property_and_bad_enum_are_skipped(caps: Capabilities) -> None:
    preset = Preset(
        name="x",
        cycles={
            "cycles.no_such_property": 1,
            "cycles.denoiser": "NOPE",
            "cycles.sampling_pattern": {"prefer": ["ALSO_NOPE"]},
            "cycles.samples": 8,
        },
        view_layer={"view_layer.use_pass_cryptomatte_object": True},
    )
    resolved = resolve_preset(preset, caps, "CYCLES")
    reasons = {s.path: s.reason for s in resolved.skipped}
    assert "not available in Blender 5.0.1" in reasons["cycles.no_such_property"]
    assert "not available" in reasons["cycles.denoiser"] and "OPENIMAGEDENOISE" in reasons["cycles.denoiser"]
    assert "none of" in reasons["cycles.sampling_pattern"]
    values = resolved.as_dict()
    assert values["cycles.samples"] == 8
    # Группа view_layer (не cycles) в capabilities не описана — оставляем, safe_set проверит.
    assert values["view_layer.use_pass_cryptomatte_object"] is True


def test_prefer_without_known_items_takes_first(caps: Capabilities) -> None:
    preset = Preset(name="x", common={"render.mystery": {"prefer": ["A", "B"]}})
    resolved = resolve_preset(preset, caps, "CYCLES")
    assert not resolved.assignments
    assert resolved.skipped[0].path == "render.mystery"
    preset = Preset(name="y", view_layer={"view_layer.mystery": {"prefer": ["A", "B"]}})
    resolved = resolve_preset(preset, caps, "CYCLES")
    assert resolved.as_dict()["view_layer.mystery"] == {"prefer": ["A", "B"]}
    assert resolved.value("view_layer.mystery") == "A"
    empty = resolve_preset(Preset(name="z", common={"render.fps": {"prefer": []}}), caps, "CYCLES")
    assert empty.skipped[0].reason == "empty prefer list"


def test_compose_overrides(caps: Capabilities, presets: dict[str, Preset]) -> None:
    resolved = resolve_preset(presets["Super"], caps, "CYCLES")
    overrides = compose_overrides(
        resolved,
        custom={"scene.cycles.samples": 64, "cycles.max_bounces": 3, "render.resolution_percentage": 25},
        untouched={"render.use_persistent_data", "scene.cycles.denoiser"},
    )
    assert overrides["cycles.samples"] == 64 and overrides["cycles.max_bounces"] == 3
    assert overrides["render.resolution_percentage"] == 25
    assert "render.use_persistent_data" not in overrides and "cycles.denoiser" not in overrides
    assert FILE_FORMAT_PATH not in overrides  # Super формат не трогает


def test_resolved_value_lookup(caps: Capabilities) -> None:
    resolved = resolve_preset(Preset(name="x", output=OutputSettings(file_format="PNG")), caps, "CYCLES")
    assert resolved.value(FILE_FORMAT_PATH) == "PNG" and resolved.value("cycles.samples") is None
