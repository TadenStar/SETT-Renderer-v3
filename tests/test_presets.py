"""Тесты core/presets.py: встроенные JSON, пользовательские, порядок, ошибки."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.presets import (
    save_user_preset,
    safe_preset_filename,
    preset_from_overrides,
    delete_user_preset,
    BUILTIN_PRESETS_DIR,
    Preset,
    PresetError,
    find_preset,
    load_preset_file,
    load_presets,
    user_presets_dir,
)

EXPECTED = ["Draft", "Super"]


def test_builtin_presets_load_in_order(tmp_path: Path) -> None:
    presets = load_presets(user_dir=tmp_path / "none")
    assert [p.name for p in presets] == EXPECTED
    assert all(p.builtin and p.source.endswith(".json") for p in presets)


def test_every_builtin_preset_has_both_engine_sections(tmp_path: Path) -> None:
    for preset in load_presets(user_dir=tmp_path / "none"):
        assert preset.cycles and preset.eevee, preset.name
        assert preset.description
        for path in (*preset.common, *preset.cycles, *preset.eevee, *preset.view_layer):
            assert "." in path and not path.startswith("scene."), f"{preset.name}: {path}"


def test_builtin_values_follow_the_settings_doc(tmp_path: Path) -> None:
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    assert presets["Draft"].cycles["cycles.adaptive_threshold"] == 0.05
    assert presets["Draft"].output.file_format == "JPEG"
    # Draft: малые сэмплы работают только вместе с денойзом (видео Blender Guru).
    assert presets["Draft"].cycles["cycles.samples"] == 64
    assert presets["Draft"].cycles["cycles.use_denoising"] is True
    assert presets["Draft"].cycles["cycles.use_fast_gi"] is True
    assert presets["Draft"].common["render.use_simplify"] is True
    # Super: запас по сэмплам, фиксированный seed против мерцания анимации.
    assert presets["Super"].cycles["cycles.samples"] == 512
    assert presets["Super"].cycles["cycles.use_animated_seed"] is False
    assert presets["Super"].cycles["cycles.seed"] == 0
    assert presets["Super"].cycles["cycles.denoising_quality"] == "HIGH"
    assert presets["Super"].warning and "20 s" in presets["Super"].warning
    assert all(p.cycles["cycles.caustics_reflective"] is False for p in presets.values())


def test_only_speed_and_delivery_presets_force_the_output_format(tmp_path: Path) -> None:
    """Формат вывода — дело сцены, если пресет не про него.

    Final раньше навязывал multilayer EXR с Cryptomatte, и на диск ложилось не то,
    что художник настроил в файле. Формат остаётся только там, где он и есть смысл
    пресета: Draft жмёт в JPEG ради скорости, Social отдаёт готовый PNG.
    """
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    assert presets["Draft"].output.file_format == "JPEG"
    assert presets["Super"].output.file_format is None
    assert presets["Super"].output.resolution_percentage is None


def test_draft_resolution_is_relative_to_the_scene(tmp_path: Path) -> None:
    """Draft берёт половину от того, что стоит в сцене, а не абсолютный процент.

    Абсолютные 50% не давали выигрыша тем, у кого в сцене уже 50%.
    """
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    assert presets["Draft"].output.resolution_scale == 0.5
    assert presets["Draft"].output.resolution_percentage is None


def test_presets_do_not_touch_tiles(tmp_path: Path) -> None:
    """Тайлы задаёт сцена: понижение tile замедляло рендер (см. hardware_tuning).

    Исключение — Heavy Scene, где мелкий тайл и есть смысл пресета.
    """
    for preset in load_presets(user_dir=tmp_path / "none"):
        assert "cycles.tile_size" not in preset.cycles, preset.name
        assert "cycles.use_auto_tile" not in preset.cycles, preset.name


def test_user_preset_overrides_builtin_and_adds_new(tmp_path: Path) -> None:
    user = tmp_path / "presets"
    user.mkdir()
    (user / "super.json").write_text(json.dumps({"name": "Super", "order": 20, "cycles": {"cycles.samples": 777}, "eevee": {"eevee.taa_render_samples": 1}}), encoding="utf-8")
    (user / "mine.json").write_text(json.dumps({"name": "Mine", "order": 5, "cycles": {"cycles.samples": 2}, "eevee": {}}), encoding="utf-8")
    presets = load_presets(user_dir=user)
    assert presets[0].name == "Mine" and not presets[0].builtin
    mine_super = find_preset(presets, "Super")
    assert mine_super is not None and mine_super.cycles["cycles.samples"] == 777 and not mine_super.builtin
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


# --- свои пресеты ------------------------------------------------------------------


def test_preset_from_overrides_splits_paths_into_sections() -> None:
    """Плоские пути из формы раскладываются по секциям пресета, вывод — в output."""
    preset = preset_from_overrides(
        "My look",
        {
            "cycles.samples": 96,
            "eevee.taa_render_samples": 32,
            "view_layer.cycles.denoising_store_passes": True,
            "render.use_simplify": True,
            "render.image_settings.file_format": "PNG",
            "render.resolution_percentage": 50,
        },
    )
    assert preset.cycles == {"cycles.samples": 96}
    assert preset.eevee == {"eevee.taa_render_samples": 32}
    assert preset.view_layer == {"view_layer.cycles.denoising_store_passes": True}
    assert preset.common == {"render.use_simplify": True}
    assert preset.output.file_format == "PNG" and preset.output.resolution_percentage == 50
    assert preset.order == 200 and not preset.builtin  # свои идут после встроенных


@pytest.mark.parametrize(
    "name, expected",
    [("My look", "My look.json"), ("Пещера", "Пещера.json"), ("a/b:c*", "a_b_c_.json"), ("  ", "preset.json")],
)
def test_safe_preset_filename(name: str, expected: str) -> None:
    assert safe_preset_filename(name) == expected


def test_save_and_delete_user_preset(tmp_path: Path) -> None:
    user = tmp_path / "presets"
    preset = preset_from_overrides("My look", {"cycles.samples": 96})
    path = save_user_preset(preset, user)
    assert path.is_file() and path.name == "My look.json"

    names = [p.name for p in load_presets(user_dir=user)]
    assert names == ["Draft", "Super", "My look"]
    loaded = find_preset(load_presets(user_dir=user), "My look")
    assert loaded is not None and loaded.cycles["cycles.samples"] == 96 and not loaded.builtin

    assert delete_user_preset("My look", user) is True
    assert delete_user_preset("My look", user) is False  # второй раз удалять нечего
    assert [p.name for p in load_presets(user_dir=user)] == ["Draft", "Super"]


def test_saving_the_same_name_twice_overwrites(tmp_path: Path) -> None:
    user = tmp_path / "presets"
    save_user_preset(preset_from_overrides("Mine", {"cycles.samples": 10}), user)
    save_user_preset(preset_from_overrides("Mine", {"cycles.samples": 20}), user)
    presets = [p for p in load_presets(user_dir=user) if p.name == "Mine"]
    assert len(presets) == 1 and presets[0].cycles["cycles.samples"] == 20


def test_deleting_a_builtin_preset_file_is_not_possible(tmp_path: Path) -> None:
    """Встроенные лежат в дистрибутиве: удаление ищет файл только в папке пользователя."""
    assert delete_user_preset("Super", tmp_path / "presets") is False
    assert {p.name for p in load_presets(user_dir=tmp_path / "presets")} == {"Draft", "Super"}
