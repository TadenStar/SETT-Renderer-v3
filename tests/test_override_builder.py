"""Тесты core/override_builder.py и safe_set из scripts/override_template.py (без Blender)."""
from __future__ import annotations

import runpy
from pathlib import Path

from brm.core.models import RenderJob
from brm.core.override_builder import (
    build_override_settings,
    render_override_script,
    write_override_script,
)
from brm.scripts import override_template as tpl


class Holder:
    """Простой владелец атрибутов вместо RNA-объекта."""


class RejectsValue:
    @property
    def device(self):
        return "CPU"

    @device.setter
    def device(self, value):
        raise TypeError(f"enum {value!r} not found in ('CPU', 'GPU')")


def test_template_import_outside_blender_does_not_run_main() -> None:
    assert tpl.bpy is None


def test_safe_set_ok() -> None:
    owner = Holder()
    owner.samples = 1
    log: list[str] = []
    assert tpl.safe_set(owner, "samples", 4, log)
    assert owner.samples == 4
    assert log == ["OK   samples = 4"]


def test_safe_set_skips_missing_attribute() -> None:
    log: list[str] = []
    assert not tpl.safe_set(Holder(), "nope", 1, log, label="scene.nope")
    assert log == ["SKIP scene.nope: not available in this Blender build"]


def test_safe_set_skips_none_owner() -> None:
    log: list[str] = []
    assert not tpl.safe_set(None, "x", 1, log)
    assert log == ["SKIP x: owner is None"]


def test_safe_set_reports_failure() -> None:
    log: list[str] = []
    assert not tpl.safe_set(RejectsValue(), "device", "OPTIX", log)
    assert log[0].startswith("FAIL device: enum 'OPTIX' not found")


def test_resolve_owner_walks_paths() -> None:
    scene = Holder()
    scene.cycles = Holder()
    scene.cycles.samples = 1
    roots = {"scene": scene, "cycles": scene.cycles}
    assert tpl.resolve_owner(roots, "scene.cycles.samples") == (scene.cycles, "samples")
    assert tpl.resolve_owner(roots, "cycles.samples") == (scene.cycles, "samples")
    assert tpl.resolve_owner(roots, "scene.nope.x") == (None, "x")
    assert tpl.resolve_owner(roots, "unknown.x") == (None, "x")
    assert tpl.resolve_owner(roots, "samples") == (None, "samples")


def test_apply_assignments_and_summary() -> None:
    scene = Holder()
    scene.cycles = Holder()
    scene.cycles.samples = 1
    scene.render = Holder()
    scene.render.fps = 24
    roots = {"scene": scene, "render": scene.render}
    log: list[str] = []
    applied = tpl.apply_assignments(
        roots, [["scene.cycles.samples", 8], ["scene.cycles.nope", 1], ["render.fps", 25]], log
    )
    assert applied == 2
    assert scene.cycles.samples == 8 and scene.render.fps == 25
    assert tpl.summarize(log) == "override applied: ok=2 skip=1 fail=0"


def test_build_override_settings_for_cycles() -> None:
    job = RenderJob(blend_path="x.blend", view_layer="VL", overrides={"scene.cycles.samples": 16})
    settings = build_override_settings(job, scene_name="Scene", engine="CYCLES", compute_device_type="OPTIX")
    assert settings["scene"] == "Scene"
    assert settings["only_view_layer"] == "VL"
    assert settings["engine"] is None  # движок из файла не трогаем
    assert settings["compute_device_type"] == "OPTIX"
    assert settings["assignments"] == [["scene.cycles.samples", 16]]
    assert settings["disable_sequencer"] is True
    assert settings["strict"] is False


def test_build_override_settings_for_eevee_has_no_device() -> None:
    job = RenderJob(blend_path="x.blend", engine="BLENDER_EEVEE")
    settings = build_override_settings(job, scene_name="S", engine="BLENDER_EEVEE", compute_device_type="OPTIX")
    assert settings["engine"] == "BLENDER_EEVEE"
    assert settings["compute_device_type"] is None


def test_render_override_script_embeds_settings_and_template() -> None:
    settings = {"scene": "Сцена", "assignments": [["scene.cycles.samples", 4]], "strict": False}
    text = render_override_script(settings)
    assert text.startswith("# Сгенерировано BRM")
    assert "def safe_set" in text and "def main" in text
    header = text.split("\n\n", 1)[0]
    namespace: dict = {}
    exec(header, namespace)  # noqa: S102 — проверяем, что заголовок валидный Python
    assert namespace["SETTINGS"] == settings


def test_write_override_script_is_valid_python(tmp_path: Path) -> None:
    path = write_override_script({"scene": "S", "assignments": []}, tmp_path / "tmp", "job1")
    assert path.parent == tmp_path / "tmp"
    assert path.name.startswith("_brm_override_job1_") and path.suffix == ".py"
    result = runpy.run_path(str(path))  # bpy нет — main() не запускается, файл просто исполняется
    assert result["SETTINGS"]["scene"] == "S"
    assert result["bpy"] is None
