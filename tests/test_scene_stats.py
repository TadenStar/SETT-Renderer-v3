"""Тесты анализа сцены и выбора вычислительного устройства."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.capabilities import (
    COMPUTE_AUTO,
    COMPUTE_CPU,
    COMPUTE_GPU,
    COMPUTE_GPU_CPU,
    Capabilities,
    device_for_mode,
)
from brm.core.scene_stats import SceneStats, SceneStatsError, analyze_scene, format_count

CAPS_FIXTURE = "capabilities_blender_5.0.1.json"


@pytest.fixture
def caps(fixtures_dir: Path) -> Capabilities:
    return Capabilities.model_validate(json.loads((fixtures_dir / CAPS_FIXTURE).read_text(encoding="utf-8")))


@pytest.fixture
def cpu_only_caps(caps: Capabilities) -> Capabilities:
    devices = [d for d in caps.cycles.devices if d.type == "CPU"]
    return caps.model_copy(update={"cycles": caps.cycles.model_copy(update={"devices": devices})})


# --- выбор устройства --------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, expected",
    [
        (COMPUTE_AUTO, ("OPTIX", False)),
        (COMPUTE_GPU, ("OPTIX", False)),
        (COMPUTE_GPU_CPU, ("OPTIX", True)),
        (COMPUTE_CPU, ("CPU", False)),
    ],
)
def test_device_for_mode(mode: str, expected: tuple[str, bool], caps: Capabilities) -> None:
    assert device_for_mode(mode, caps) == expected


@pytest.mark.parametrize("mode", [COMPUTE_AUTO, COMPUTE_GPU, COMPUTE_GPU_CPU, COMPUTE_CPU])
def test_without_a_gpu_every_mode_falls_back_to_cpu(mode: str, cpu_only_caps: Capabilities) -> None:
    """Честнее посчитать на процессоре, чем притвориться, что считаем на карте."""
    device, use_cpu = device_for_mode(mode, cpu_only_caps)
    assert device == "CPU" and use_cpu is False


def test_unknown_mode_behaves_like_auto(caps: Capabilities) -> None:
    """Настройка из файла может оказаться любой строкой — это не повод падать."""
    assert device_for_mode("nonsense", caps) == device_for_mode(COMPUTE_AUTO, caps)


# --- статистика сцены --------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [(0, "0"), (999, "999"), (1000, "1.0 k"), (63048, "63.0 k"), (1_250_000, "1.2 M")],
)
def test_format_count(value: int, expected: str) -> None:
    assert format_count(value) == expected


def test_summary_names_objects_geometry_and_instances() -> None:
    stats = SceneStats(
        objects=9, meshes=3, triangles=63048, instances=4, instanced_triangles=48, camera_culled_objects=1
    )
    summary = stats.summary()
    assert "9 objects" in summary and "63.0 k triangles" in summary
    assert "4 instances" in summary and "camera cull" in summary


def test_summary_says_when_there_are_no_instances() -> None:
    assert "no instances" in SceneStats(objects=2, triangles=100).summary()


def test_instanced_share_survives_an_empty_scene() -> None:
    """Пустая сцена не должна делить на ноль."""
    assert SceneStats().instanced_share == 0.0
    assert SceneStats(triangles=100, instanced_triangles=25).instanced_share == 0.25


def test_analysis_of_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SceneStatsError, match="File not found"):
        analyze_scene("blender.exe", tmp_path / "gone.blend", tmp_dir=tmp_path / "tmp")


def test_unknown_fields_in_the_probe_output_are_ignored() -> None:
    """Новая версия пробы может добавить ключи — старое приложение их переживёт."""
    stats = SceneStats.model_validate({"objects": 3, "triangles": 10, "something_new": 42})
    assert stats.objects == 3 and stats.triangles == 10
