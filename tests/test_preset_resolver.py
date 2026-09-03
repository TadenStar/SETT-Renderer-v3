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
    resolved = resolve_preset(presets["Balanced"], caps, "CYCLES")
    assert resolved.engine == "CYCLES" and resolved.file_format == "PNG"
    assert resolved.assignments[0] == (FILE_FORMAT_PATH, "PNG")
    values = resolved.as_dict()
    assert values["render.image_settings.color_depth"] == "16"
    assert values["render.image_settings.compression"] == 15
    assert values["cycles.samples"] == 1024
    # prefer: в форме — первый статически доступный, в override — хвост списка с него.
    assert resolved.value("cycles.sampling_pattern") == "BLUE_NOISE"
    assert values["cycles.sampling_pattern"] == {"prefer": ["BLUE_NOISE", "TABULATED_SOBOL", "SOBOL_BURLEY", "AUTOMATIC"]}
    assert resolved.value("cycles.denoiser") == "OPENIMAGEDENOISE"
    assert values["cycles.denoiser"] == {"prefer": ["OPENIMAGEDENOISE", "OPTIX"]}
    assert resolved.value("scene.cycles.samples") == 1024
    assert values["render.use_persistent_data"] is True
    assert not [path for path in values if path.startswith("eevee.")]
    for skipped in resolved.skipped:
        assert skipped.reason
    unavailable = {s.path for s in resolved.skipped}
    assert "cycles.samples" not in unavailable


def test_balanced_for_eevee_uses_eevee_section(caps: Capabilities, presets: dict[str, Preset]) -> None:
    resolved = resolve_preset(presets["Balanced"], caps, "BLENDER_EEVEE")
    values = resolved.as_dict()
    assert values["eevee.taa_render_samples"] == 64
    assert values["eevee.ray_tracing_options.resolution_scale"] == "2"
    assert not [path for path in values if path.startswith("cycles.")]
    assert values["render.use_persistent_data"] is True


def test_output_sections_per_format(caps: Capabilities, presets: dict[str, Preset]) -> None:
    draft = resolve_preset(presets["Draft"], caps, "CYCLES").as_dict()
    assert draft[FILE_FORMAT_PATH] == "JPEG" and draft["render.image_settings.quality"] == 90
    assert "render.image_settings.compression" not in draft
    assert draft["render.resolution_percentage"] == 50 and draft["cycles.time_limit"] == 20

    final_resolved = resolve_preset(presets["Final"], caps, "CYCLES")
    final = final_resolved.as_dict()
    # Статический список 5.0.1 ещё содержит OPEN_EXR_MULTILAYER, поэтому показываем его,
    # а в override уходит список кандидатов: внутри Blender сработает запасной OPEN_EXR.
    assert final_resolved.file_format == "OPEN_EXR_MULTILAYER"
    assert final_resolved.value(FILE_FORMAT_PATH) == "OPEN_EXR_MULTILAYER"
    assert final[FILE_FORMAT_PATH] == {"prefer": ["OPEN_EXR_MULTILAYER", "OPEN_EXR"]}
    assert final["render.image_settings.exr_codec"] == "ZIP"
    assert "render.image_settings.compression" not in final
    assert display_file_format(final, "PNG") == "OPEN_EXR_MULTILAYER"
    assert display_file_format({}, "PNG") == "PNG"

    social = resolve_preset(presets["Social 9:16"], caps, "CYCLES").as_dict()
    assert (social["render.resolution_x"], social["render.resolution_y"], social["render.fps"]) == (1080, 1920, 30)


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
    resolved = resolve_preset(presets["Balanced"], caps, "CYCLES")
    overrides = compose_overrides(
        resolved,
        custom={"scene.cycles.samples": 64, "cycles.max_bounces": 3, "render.resolution_percentage": 25},
        untouched={"render.use_persistent_data", "scene.cycles.denoiser"},
    )
    assert overrides["cycles.samples"] == 64 and overrides["cycles.max_bounces"] == 3
    assert overrides["render.resolution_percentage"] == 25
    assert "render.use_persistent_data" not in overrides and "cycles.denoiser" not in overrides
    assert list(overrides)[0] == FILE_FORMAT_PATH  # порядок: формат первым


def test_resolved_value_lookup(caps: Capabilities) -> None:
    resolved = resolve_preset(Preset(name="x", output=OutputSettings(file_format="PNG")), caps, "CYCLES")
    assert resolved.value(FILE_FORMAT_PATH) == "PNG" and resolved.value("cycles.samples") is None
