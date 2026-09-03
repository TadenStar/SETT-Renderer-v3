"""Тесты core/project_probe.py на сохранённом выводе реальной пробы (tests/fixtures)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core import project_probe as probe_mod
from brm.core.blender_process import BlenderResult
from brm.core.project_probe import (
    ProjectInfo,
    ProjectProbeError,
    SceneInfo,
    probe_project,
    project_warnings,
)

FIXTURE = "project_default_scene_5.0.1.json"


@pytest.fixture
def project_fixture(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / FIXTURE).read_text(encoding="utf-8"))


@pytest.fixture
def blend_file(tmp_path: Path) -> Path:
    blend = tmp_path / "Сцена test.blend"
    blend.write_bytes(b"BLENDER-v500")
    return blend


def _fake_run(fixture: dict, *, ok: bool = True, write: bool = True):
    def fake(blender_path, args, **kwargs):
        out = Path(str(args[-1]))
        if write:
            out.write_text(json.dumps(fixture), encoding="utf-8")
        return BlenderResult(argv=[str(blender_path), *map(str, args)], returncode=0 if ok else 1, stdout="[BRM] fake", duration=0.1)

    return fake


def test_fixture_parses_into_model(project_fixture: dict) -> None:
    info = ProjectInfo.model_validate(project_fixture)
    scene = info.default_scene()
    assert scene is not None and scene.name == "Scene"
    assert scene.frame_start == 1 and scene.frame_end == 250
    assert scene.cameras == ["Camera"] and scene.active_camera == "Camera"
    assert scene.final_resolution == (1920, 1080)
    assert scene.frame_count == 250
    assert [vl.name for vl in scene.view_layers] == ["ViewLayer"]
    assert info.saved_with_version >= (4, 2, 0)
    assert info.scene("nope") is None


def test_scene_frame_count_and_resolution() -> None:
    scene = SceneInfo(name="s", frame_start=10, frame_end=1)
    assert scene.frame_count == 0
    scene = SceneInfo(name="s", frame_start=1, frame_end=10, frame_step=3, resolution_x=1000, resolution_y=500, resolution_percentage=50)
    assert scene.frame_count == 4
    assert scene.final_resolution == (500, 250)


def test_warnings_for_clean_file(project_fixture: dict) -> None:
    info = ProjectInfo.model_validate(project_fixture)
    assert info.saved_with_version == info.blender_version_file
    assert project_warnings(info) == []


def test_warning_when_file_is_newer_than_binary(project_fixture: dict) -> None:
    info = ProjectInfo.model_validate(project_fixture)
    info.saved_with_version = (5, 2, 7)
    info.blender_version_file = (5, 0, 119)
    info.blender_version = (5, 0, 1)
    warnings = project_warnings(info)
    assert len(warnings) == 1
    assert "saved with Blender 5.2" in warnings[0]
    assert "newer than the selected Blender 5.0.1" in warnings[0]


def test_no_version_warning_when_file_version_unknown() -> None:
    info = ProjectInfo(file_path="x.blend", saved_with_version=(9, 9, 9))
    assert project_warnings(info) == []


def test_warnings_for_video_output_sequencer_and_missing_libs() -> None:
    info = ProjectInfo(
        file_path="x.blend",
        scenes=[
            SceneInfo(name="Shot", file_format="FFMPEG", use_sequencer=True, sequencer_strips=3, cameras=["Cam"]),
            SceneInfo(name="Empty", frame_start=5, frame_end=1),
        ],
        missing_libraries=["//lib/a.blend", "//lib/b.blend", "//lib/c.blend", "//lib/d.blend"],
    )
    text = "\n".join(project_warnings(info))
    assert "FFMPEG" in text
    assert "3 sequencer strips" in text
    assert "empty frame range" in text
    assert "no camera" in text
    assert "and 1 more" in text


def test_probe_project_reads_output(tmp_path: Path, fake_blender: Path, blend_file: Path, project_fixture: dict, monkeypatch) -> None:
    seen: dict = {}

    def spy(blender_path, args, **kwargs):
        seen["args"] = [str(a) for a in args]
        return _fake_run(project_fixture)(blender_path, args, **kwargs)

    monkeypatch.setattr(probe_mod, "run_blender", spy)
    info = probe_project(fake_blender, blend_file, tmp_dir=tmp_path / "tmp")
    assert info.default_scene().name == "Scene"
    args = seen["args"]
    assert args[:2] == ["-b", str(blend_file)]
    assert "--factory-startup" not in args
    assert args[2:4] == ["--python-exit-code", "1"]
    assert args[5].endswith("probe_scene.py")
    assert list((tmp_path / "tmp").iterdir()) == []


def test_probe_project_missing_file(tmp_path: Path, fake_blender: Path) -> None:
    with pytest.raises(ProjectProbeError, match="not found"):
        probe_project(fake_blender, tmp_path / "missing.blend", tmp_dir=tmp_path)


def test_probe_project_failure_raises(tmp_path: Path, fake_blender: Path, blend_file: Path, project_fixture: dict, monkeypatch) -> None:
    monkeypatch.setattr(probe_mod, "run_blender", _fake_run(project_fixture, ok=False))
    with pytest.raises(ProjectProbeError, match="exit code 1"):
        probe_project(fake_blender, blend_file, tmp_dir=tmp_path)


def test_probe_project_unreadable_output(tmp_path: Path, fake_blender: Path, blend_file: Path, monkeypatch) -> None:
    monkeypatch.setattr(probe_mod, "run_blender", _fake_run({"scenes": "oops"}))
    with pytest.raises(ProjectProbeError, match="unreadable"):
        probe_project(fake_blender, blend_file, tmp_dir=tmp_path)
