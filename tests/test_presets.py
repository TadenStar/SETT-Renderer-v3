"""Тесты core/presets.py: встроенные JSON, пользовательские, порядок, ошибки."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.presets import (
    BUILTIN_PRESETS_DIR,
    Preset,
    PresetError,
    find_preset,
    load_preset_file,
    load_presets,
    user_presets_dir,
)

EXPECTED = ["Draft", "Preview", "Balanced", "Final", "Heavy Scene", "Anti-flicker", "Social 9:16"]


def test_builtin_presets_load_in_order(tmp_path: Path) -> None:
    presets = load_presets(user_dir=tmp_path / "none")
    assert [p.name for p in presets] == EXPECTED
    assert all(p.builtin and p.source.endswith(".json") for p in presets)


def test_every_builtin_preset_has_both_engine_sections(tmp_path: Path) -> None:
    for preset in load_presets(user_dir=tmp_path / "none"):
        assert preset.cycles and preset.eevee, preset.name
        assert preset.output.file_format, preset.name
        assert preset.description
        for path in (*preset.common, *preset.cycles, *preset.eevee, *preset.view_layer):
            assert "." in path and not path.startswith("scene."), f"{preset.name}: {path}"


def test_builtin_values_follow_the_settings_doc(tmp_path: Path) -> None:
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    assert presets["Draft"].cycles["cycles.adaptive_threshold"] == 0.05
    assert presets["Draft"].cycles["cycles.time_limit"] == 20
    assert presets["Draft"].output.file_format == "JPEG" and presets["Draft"].output.resolution_percentage == 50
    assert presets["Balanced"].cycles["cycles.samples"] == 1024
    assert presets["Balanced"].cycles["cycles.adaptive_min_samples"] == 32
    assert presets["Final"].cycles["cycles.adaptive_threshold"] == 0.005
    assert presets["Final"].output.file_format == {"prefer": ["OPEN_EXR_MULTILAYER", "OPEN_EXR"]}
    assert presets["Final"].view_layer["view_layer.cycles.denoising_store_passes"] is True
    assert presets["Heavy Scene"].common["render.use_persistent_data"] is False
    assert presets["Heavy Scene"].cycles["cycles.tile_size"] == 512 and presets["Heavy Scene"].chunk_size == 20
    assert presets["Anti-flicker"].cycles["cycles.use_animated_seed"] is False
    assert presets["Social 9:16"].output.resolution_x == 1080 and presets["Social 9:16"].output.fps == 30
    assert all(p.cycles["cycles.caustics_reflective"] is False for p in presets.values())


def test_user_preset_overrides_builtin_and_adds_new(tmp_path: Path) -> None:
    user = tmp_path / "presets"
    user.mkdir()
    (user / "balanced.json").write_text(json.dumps({"name": "Balanced", "order": 30, "cycles": {"cycles.samples": 777}, "eevee": {"eevee.taa_render_samples": 1}}), encoding="utf-8")
    (user / "mine.json").write_text(json.dumps({"name": "Mine", "order": 5, "cycles": {"cycles.samples": 2}, "eevee": {}}), encoding="utf-8")
    presets = load_presets(user_dir=user)
    assert presets[0].name == "Mine" and not presets[0].builtin
    balanced = find_preset(presets, "Balanced")
    assert balanced is not None and balanced.cycles["cycles.samples"] == 777 and not balanced.builtin
    assert len(presets) == len(EXPECTED) + 1


def test_broken_user_preset_is_skipped_unless_strict(tmp_path: Path) -> None:
    user = tmp_path / "presets"
    user.mkdir()
    (user / "broken.json").write_text("{ nope", encoding="utf-8")
    assert [p.name for p in load_presets(user_dir=user)] == EXPECTED
    with pytest.raises(PresetError):
        load_presets(user_dir=user, strict=True)
    with pytest.raises(PresetError):
        load_preset_file(user / "broken.json")


def test_preset_validation(tmp_path: Path) -> None:
    file = tmp_path / "x.json"
    file.write_text(json.dumps({"order": 1}), encoding="utf-8")  # нет name
    with pytest.raises(PresetError):
        load_preset_file(file)
    assert Preset(name="X").output.file_format is None
    assert find_preset([], "X") is None


def test_dirs() -> None:
    assert BUILTIN_PRESETS_DIR.is_dir()
    assert user_presets_dir().name == "presets"
