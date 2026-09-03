"""Тесты core/render_plan.py на фикстурах capabilities и проекта."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from brm.core.capabilities import Capabilities
from brm.core.frame_range import FrameRange, FrameRangeMode
from brm.core.models import RenderJob
from brm.core.project_probe import ProjectInfo
from brm.core.render_plan import build_render_plan, log_file_name
from brm.core.storage import AppSettings


@pytest.fixture
def caps(fixtures_dir: Path) -> Capabilities:
    data = json.loads((fixtures_dir / "capabilities_blender_5.0.1.json").read_text(encoding="utf-8"))
    caps = Capabilities.model_validate(data)
    caps.blender_path = r"C:\Blender\blender.exe"
    return caps


@pytest.fixture
def project(fixtures_dir: Path) -> ProjectInfo:
    data = json.loads((fixtures_dir / "project_default_scene_5.0.1.json").read_text(encoding="utf-8"))
    info = ProjectInfo.model_validate(data)
    info.file_path = r"D:\shots\cave.blend"
    return info


def test_plan_for_eevee_scene_from_file(caps: Capabilities, project: ProjectInfo, tmp_path: Path) -> None:
    job = RenderJob(blend_path=project.file_path, scene="Scene")
    settings = AppSettings(default_output_dir=r"D:\out")
    plan = build_render_plan(job, caps, settings, project, tmp_dir=tmp_path / "tmp")

    assert plan.frames == list(range(1, 251))
    assert plan.engine == "BLENDER_EEVEE" and plan.cycles_device is None
    assert plan.output_path == os.path.normpath(r"D:\out\cave\Scene\####")
    assert plan.output_dir == Path(os.path.normpath(r"D:\out\cave\Scene"))
    assert plan.log_path.parent == plan.output_dir and plan.log_path.name.startswith("render_log_")
    argv = plan.argv
    assert argv[:3] == [caps.blender_path, "-b", project.file_path]
    assert argv[3:5] == ["-S", "Scene"]
    assert argv[argv.index("--python") + 1] == str(plan.override_script)
    assert argv[-5:] == ["-s", "1", "-e", "250", "-a"]
    assert "--" not in argv
    assert plan.override_script.is_file()
    assert plan.override_settings["scene"] == "Scene"
    assert plan.override_settings["compute_device_type"] is None
    assert plan.command_line.startswith(caps.blender_path)


def test_plan_for_cycles_uses_best_device_and_overrides(caps: Capabilities, project: ProjectInfo, tmp_path: Path) -> None:
    job = RenderJob(
        blend_path=project.file_path,
        engine="CYCLES",
        frame_range=FrameRange(mode=FrameRangeMode.SINGLE, frame=12),
        overrides={"scene.cycles.samples": 8},
    )
    plan = build_render_plan(job, caps, AppSettings(), project, tmp_dir=tmp_path)
    assert plan.frames == [12]
    assert plan.engine == "CYCLES" and plan.cycles_device == "OPTIX"
    assert plan.argv[-4:] == ["--", "--cycles-device", "OPTIX", "--cycles-print-stats"]
    assert plan.argv[plan.argv.index("--render-frame") + 1] == "12"
    assert plan.override_settings["engine"] == "CYCLES"
    assert plan.override_settings["compute_device_type"] == "OPTIX"
    assert plan.override_settings["assignments"] == [["scene.cycles.samples", 8]]
    # Без папки вывода в настройках — рядом с .blend.
    assert plan.output_path == os.path.normpath(r"D:\shots\cave\Scene\####")


def test_plan_falls_back_to_default_scene(caps: Capabilities, project: ProjectInfo, tmp_path: Path) -> None:
    job = RenderJob(blend_path=project.file_path, scene="Missing")
    plan = build_render_plan(job, caps, AppSettings(), project, tmp_dir=tmp_path)
    assert plan.scene is not None and plan.scene.name == "Scene"


def test_plan_without_scenes_raises(caps: Capabilities, tmp_path: Path) -> None:
    empty = ProjectInfo(file_path="x.blend")
    with pytest.raises(ValueError, match="no scenes"):
        build_render_plan(RenderJob(blend_path="x.blend"), caps, AppSettings(), empty, tmp_dir=tmp_path)


def test_plan_invalid_frame_list_raises(caps: Capabilities, project: ProjectInfo, tmp_path: Path) -> None:
    job = RenderJob(blend_path=project.file_path, frame_range=FrameRange(mode=FrameRangeMode.LIST, frames_text="1..x"))
    with pytest.raises(ValueError):
        build_render_plan(job, caps, AppSettings(), project, tmp_dir=tmp_path)


def test_log_file_name_has_timestamp() -> None:
    from datetime import datetime

    assert log_file_name(datetime(2026, 9, 3, 14, 5, 9)) == "render_log_20260903-140509.txt"
