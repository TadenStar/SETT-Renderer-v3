"""Интеграционные тесты с настоящим blender.exe (маркер ``blender``).

Пропускаются, если Blender не найден. Путь можно задать через ``BRM_BLENDER``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from brm.core.blender_process import run_blender
from brm.core.capabilities import get_capabilities, run_probe, support_problem
from brm.core.frame_range import FrameRange, FrameRangeMode
from brm.core.models import RenderJob
from brm.core.output_scan import scan_output
from brm.core.preset_resolver import compose_overrides, resolve_preset
from brm.core.presets import load_presets
from brm.core.project_probe import probe_project, project_warnings
from brm.core.render_plan import build_render_plan
from brm.core.render_stats import RenderTracker
from brm.core.storage import AppSettings

pytestmark = pytest.mark.blender


@pytest.fixture(scope="module")
def tiny_blend(real_blender: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Крошечный .blend: сцена по умолчанию, сохранённая самим Blender."""
    target = tmp_path_factory.mktemp("blend") / "Тест default.blend"
    expr = f"import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'{target}')"
    result = run_blender(real_blender, ["-b", "--factory-startup", "--python-expr", expr], timeout=120)
    assert result.ok, result.tail()
    assert target.is_file()
    return target


def test_capabilities_probe_on_real_blender(real_blender: str, tmp_path: Path) -> None:
    caps = run_probe(real_blender, tmp_dir=tmp_path / "tmp", timeout=180)
    assert support_problem(caps) is None
    assert caps.has_engine("CYCLES")
    assert caps.eevee_engine_id is not None and caps.has_engine(caps.eevee_engine_id)
    assert caps.cycles.available
    assert "NONE" in caps.cycles.compute_device_types
    assert caps.property("cycles", "samples").type == "INT"
    assert caps.property("cycles", "adaptive_threshold").soft_max == 1.0
    assert caps.property("render", "resolution_x").factory_value == 1920
    denoiser = caps.property("cycles", "denoiser")
    assert denoiser.enum_dynamic and "OPENIMAGEDENOISE" in denoiser.enum_identifiers()
    assert denoiser.factory_value in denoiser.enum_identifiers()
    assert not caps.property("image_settings", "file_format").enum_dynamic
    assert "PNG" in caps.property("image_settings", "file_format").enum_identifiers()
    assert not (tmp_path / "tmp").exists() or list((tmp_path / "tmp").iterdir()) == []


def test_capabilities_cache_written_and_reused(real_blender: str, tmp_path: Path) -> None:
    caps = get_capabilities(real_blender, cache_dir=tmp_path / "cache", tmp_dir=tmp_path / "tmp", timeout=180)
    files = list((tmp_path / "cache").glob("capabilities_*.json"))
    assert len(files) == 1
    again = get_capabilities(real_blender, cache_dir=tmp_path / "cache", tmp_dir=tmp_path / "tmp", timeout=1)
    assert again.probed_at == caps.probed_at  # пришло из кэша, проба не повторялась


def test_project_probe_on_default_scene(real_blender: str, tiny_blend: Path, tmp_path: Path) -> None:
    info = probe_project(real_blender, tiny_blend, tmp_dir=tmp_path / "tmp", timeout=180)
    scene = info.default_scene()
    assert scene is not None and scene.name == "Scene"
    assert (scene.frame_start, scene.frame_end) == (1, 250)
    assert scene.cameras == ["Camera"] and scene.active_camera == "Camera"
    assert scene.final_resolution == (1920, 1080)
    assert scene.fps == 24
    assert [vl.name for vl in scene.view_layers] == ["ViewLayer"]
    assert info.saved_with_version == info.blender_version_file
    assert info.blender_version[:2] == info.blender_version_file[:2]
    assert Path(info.file_path).name == tiny_blend.name
    assert project_warnings(info) == []


def test_render_one_frame_with_override(real_blender: str, tiny_blend: Path, tmp_path: Path) -> None:
    """Критерий M2: один кадр рендерится, override применился, PNG на диске."""
    caps = get_capabilities(real_blender, cache_dir=tmp_path / "cache", tmp_dir=tmp_path / "tmp", timeout=180)
    info = probe_project(real_blender, tiny_blend, tmp_dir=tmp_path / "tmp", timeout=180)
    job = RenderJob(
        blend_path=str(tiny_blend),
        engine="CYCLES",
        frame_range=FrameRange(mode=FrameRangeMode.SINGLE, frame=1),
        overrides={
            "scene.render.resolution_x": 64,
            "scene.render.resolution_y": 64,
            "scene.render.resolution_percentage": 100,
            "scene.cycles.samples": 4,
            "scene.cycles.use_denoising": False,
            "scene.cycles.no_such_property": 1,  # должно попасть в SKIP, а не уронить рендер
        },
    )
    settings = AppSettings(default_output_dir=str(tmp_path / "out"))
    plan = build_render_plan(job, caps, settings, info, tmp_dir=tmp_path / "tmp")

    result = run_blender(plan.argv[0], plan.argv[1:], timeout=600)
    assert result.ok, result.tail(60)
    brm = result.brm_lines()
    assert "[BRM] OK   scene.render.engine = 'CYCLES'" in brm
    assert "[BRM] OK   scene.cycles.samples = 4" in brm
    assert "[BRM] SKIP scene.cycles.no_such_property: not available in this Blender build" in brm
    assert any("override applied: ok=" in line and "fail=0" in line for line in brm)
    png = plan.output_dir / "0001.png"
    assert png.is_file() and png.stat().st_size > 0
    assert "Saved:" in result.stdout


def test_resume_after_a_killed_render(real_blender: str, tiny_blend: Path, tmp_path: Path) -> None:
    """Критерий M5: половина кадров на диске, приложение дорендеривает остаток само."""
    caps = get_capabilities(real_blender, cache_dir=tmp_path / "cache", tmp_dir=tmp_path / "tmp", timeout=180)
    info = probe_project(real_blender, tiny_blend, tmp_dir=tmp_path / "tmp", timeout=180)
    job = RenderJob(
        blend_path=str(tiny_blend),
        frame_range=FrameRange(mode=FrameRangeMode.MANUAL, start=1, end=6),
        overrides={"scene.render.resolution_x": 64, "scene.render.resolution_y": 64, "scene.eevee.taa_render_samples": 4},
        resume=True,
        min_frame_kb=1,
    )
    settings = AppSettings(default_output_dir=str(tmp_path / "out"))

    # Первый прогон: только кадры 1..3, как будто остальные не успели.
    first = build_render_plan(job, caps, settings, info, tmp_dir=tmp_path / "tmp", frames_override=[1, 2, 3])
    assert run_blender(first.argv[0], first.argv[1:], timeout=600).ok
    on_disk = sorted(p.name for p in first.output_dir.glob("*.png"))
    assert on_disk == ["0001.png", "0002.png", "0003.png"]
    # Кадр 2 «битый»: пустой файл, resume обязан его перерендерить.
    (first.output_dir / "0002.png").write_bytes(b"")

    scan = scan_output(first.output_path, [1, 2, 3, 4, 5, 6], extensions=["png"])
    missing = scan.missing([1, 2, 3, 4, 5, 6], min_size_bytes=1024)
    assert missing == [2, 4, 5, 6]

    second = build_render_plan(job, caps, settings, info, tmp_dir=tmp_path / "tmp", frames_override=missing)
    assert second.argv[second.argv.index("--render-frame") + 1] == "2,4..6"
    result = run_blender(second.argv[0], second.argv[1:], timeout=600)
    assert result.ok, result.tail(40)
    assert sorted(p.name for p in second.output_dir.glob("*.png")) == [f"{f:04d}.png" for f in range(1, 7)]
    assert all(p.stat().st_size > 1024 for p in second.output_dir.glob("*.png"))


def test_presets_draft_and_final_change_the_render(real_blender: str, tiny_blend: Path, tmp_path: Path) -> None:
    """Критерий M4: пресет меняет сэмплы, формат и время кадра, ничего не падает в FAIL."""
    caps = get_capabilities(real_blender, cache_dir=tmp_path / "cache", tmp_dir=tmp_path / "tmp", timeout=180)
    info = probe_project(real_blender, tiny_blend, tmp_dir=tmp_path / "tmp", timeout=180)
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    small = {"scene.render.resolution_x": 96, "scene.render.resolution_y": 64, "render.resolution_percentage": 100}
    results = {}
    for name in ("Draft", "Final"):
        resolved = resolve_preset(presets[name], caps, "CYCLES")
        overrides = compose_overrides(resolved, custom=small)
        job = RenderJob(
            blend_path=str(tiny_blend),
            engine="CYCLES",
            preset=name,
            file_format=resolved.file_format or "PNG",
            frame_range=FrameRange(mode=FrameRangeMode.SINGLE, frame=1),
            overrides=overrides,
            output_template="{output_dir}/{preset}/####",
        )
        plan = build_render_plan(job, caps, AppSettings(default_output_dir=str(tmp_path / "out")), info, tmp_dir=tmp_path / "tmp")
        result = run_blender(plan.argv[0], plan.argv[1:], timeout=900)
        assert result.ok, result.tail(60)
        assert not [line for line in result.brm_lines() if line.startswith("[BRM] FAIL")], result.brm_lines()
        tracker = RenderTracker(plan.frames)
        for line in result.stdout.splitlines():
            tracker.feed(line)
        results[name] = (tracker.progress, plan, result.stdout)

    draft, draft_plan, draft_log = results["Draft"]
    final, final_plan, final_log = results["Final"]
    assert draft.samples_total == 128 and final.samples_total == 4096
    assert draft.frame_times() and final.frame_times()
    assert (draft_plan.output_dir / "0001.jpg").is_file()
    assert (final_plan.output_dir / "0001.exr").is_file()
    assert "[BRM] OK   cycles.time_limit = 20" in draft_log
    assert "[BRM] OK   view_layer.cycles.denoising_store_passes = True" in final_log
    assert "[BRM] OK   cycles.sampling_pattern = 'BLUE_NOISE'" in final_log
    # 5.0: OPEN_EXR_MULTILAYER отвергнут, сработал запасной OPEN_EXR; в 4.x пройдёт первый.
    assert "[BRM] OK   render.image_settings.file_format = 'OPEN_EXR" in final_log
