"""Тесты пробы железа и подстройки пресета под него."""
from __future__ import annotations

import os
import subprocess
import threading

import pytest

from brm.core import hardware as hardware_mod
from brm.core.chunking import OOM_RETRY_STEPS
from brm.core.hardware import (
    HardwareInfo,
    detect_gpu,
    detect_hardware,
    detect_ram_mb,
    parse_nvidia_smi,
    round_gb,
)
from brm.core.hardware_tuning import (
    DENOISING_USE_GPU,
    PERSISTENT_DATA,
    tier_for_vram,
    tune_preset,
)
from brm.core.presets import Preset

# --- проба ---------------------------------------------------------------------


def test_parse_nvidia_smi_normal_output() -> None:
    assert parse_nvidia_smi("NVIDIA GeForce RTX 5070 Laptop GPU, 8151\n") == (
        "NVIDIA GeForce RTX 5070 Laptop GPU",
        8151,
    )


def test_parse_nvidia_smi_takes_the_first_card() -> None:
    output = "NVIDIA A, 24576\nNVIDIA B, 8192\n"
    assert parse_nvidia_smi(output) == ("NVIDIA A", 24576)


@pytest.mark.parametrize(
    "output, expected",
    [
        ("", ("", None)),
        ("\n\n", ("", None)),
        ("NVIDIA Card, [N/A]", ("NVIDIA Card", None)),  # драйвер не отдал объём
        ("NVIDIA Card", ("NVIDIA Card", None)),  # запятой нет вовсе
        (", 8192", ("", None)),  # имени нет — доверять нечему
        ("Failed to initialize NVML: Driver/library version mismatch", None),
    ],
)
def test_parse_nvidia_smi_degrades_softly(output: str, expected) -> None:
    """Мусор на входе не должен ронять пробу — максимум оставить поля пустыми."""
    name, vram = parse_nvidia_smi(output)
    if expected is None:  # текст ошибки: имя какое-то будет, памяти нет
        assert vram is None
    else:
        assert (name, vram) == expected


@pytest.mark.parametrize("mb, expected", [(8151, 8), (4096, 4), (16384, 16), (12288, 12), (24564, 24)])
def test_round_gb_reports_what_the_box_says(mb: int, expected: int) -> None:
    """8151 MiB — карта на 8 ГБ, а не на 7.96: пороги считаются по округлённому объёму."""
    assert round_gb(mb) == expected


def test_summary_and_device_match() -> None:
    info = HardwareInfo(gpu_name="NVIDIA GeForce RTX 5070 Laptop GPU", vram_mb=8151, ram_mb=32189, cpu_threads=24)
    assert info.summary() == "NVIDIA GeForce RTX 5070 Laptop GPU (8 GB VRAM) · 31 GB RAM · 24 threads"
    assert info.is_known()
    assert info.matches_render_device(["NVIDIA GeForce RTX 5070 Laptop GPU", "Intel Core Ultra 9"]) is True
    assert info.matches_render_device(["NVIDIA GeForce RTX 4090"]) is False
    assert info.matches_render_device([]) is None
    assert HardwareInfo().matches_render_device(["Any"]) is None


def test_empty_hardware_is_a_working_state() -> None:
    info = HardwareInfo()
    assert not info.is_known() and info.summary() == "Hardware unknown"
    assert info.vram_gb is None and info.ram_gb is None


def test_detect_gpu_without_nvidia_smi_explains_itself(monkeypatch) -> None:
    monkeypatch.setattr(hardware_mod, "nvidia_smi_path", lambda: None)
    name, vram, note = detect_gpu()
    assert (name, vram) == ("", None) and "nvidia-smi not found" in note


def test_detect_gpu_survives_a_broken_binary(monkeypatch) -> None:
    monkeypatch.setattr(hardware_mod, "nvidia_smi_path", lambda: "nvidia-smi")

    def boom(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(hardware_mod.subprocess, "run", boom)
    name, vram, note = detect_gpu()
    assert (name, vram) == ("", None) and "access denied" in note


def test_detect_gpu_reports_a_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(hardware_mod, "nvidia_smi_path", lambda: "nvidia-smi")
    result = subprocess.CompletedProcess([], 9, stdout="", stderr="NVML error\n")
    monkeypatch.setattr(hardware_mod.subprocess, "run", lambda *a, **k: result)
    assert detect_gpu() == ("", None, "NVML error")


def test_detect_hardware_skips_the_gpu_probe_when_cancelled(monkeypatch) -> None:
    """Отмена приходит при закрытии окна: запускать nvidia-smi уже незачем."""
    called = []
    monkeypatch.setattr(hardware_mod, "detect_gpu", lambda: called.append(1) or ("X", 1024, ""))
    cancel = threading.Event()
    cancel.set()
    info = detect_hardware(cancel=cancel)
    assert called == [] and info.vram_mb is None


def test_detect_hardware_on_this_machine() -> None:
    """Проба обязана возвращать объект, а не бросаться исключениями, на любой машине."""
    info = detect_hardware()
    assert info.cpu_threads >= 1
    if os.name == "nt":
        ram_mb, note = detect_ram_mb()
        assert note == "" and ram_mb is not None and ram_mb > 1024


# --- подстройка ------------------------------------------------------------------


def cycles_preset(**cycles) -> Preset:
    return Preset(name="T", cycles=dict(cycles), common={PERSISTENT_DATA: True})


@pytest.mark.parametrize(
    "vram_gb, denoise_on_cpu",
    [(4, True), (7, True), (8, False), (12, False), (24, False)],
)
def test_vram_tiers(vram_gb: int, denoise_on_cpu: bool) -> None:
    assert tier_for_vram(vram_gb).denoise_on_cpu is denoise_on_cpu


def test_tuning_never_touches_tiles() -> None:
    """Регрессия: понижение tile под VRAM замедляло рендер.

    Кадр 1920x1920 влезает в тайл 2048 целиком, а в 1024 бьётся на четыре куска
    и считается на 6.6% дольше (замер на живом Blender). Тайлы задаёт сцена
    или пользователь, подстройка под железо в это не лезет.
    """
    preset = cycles_preset(**{"cycles.tile_size": 2048, "cycles.use_auto_tile": True})
    for vram_mb in (2048, 4096, 8151, 12288, 24576):
        result = tune_preset(preset, HardwareInfo(vram_mb=vram_mb, ram_mb=8192))
        assert "cycles.tile_size" not in result.changes
        assert "cycles.use_auto_tile" not in result.changes
        assert result.preset.cycles["cycles.tile_size"] == 2048


def test_tuning_never_undoes_a_tighter_preset() -> None:
    """Heavy Scene гасит Persistent Data осознанно — включать обратно нельзя."""
    preset = Preset(name="T", cycles={DENOISING_USE_GPU: False}, common={PERSISTENT_DATA: False})
    result = tune_preset(preset, HardwareInfo(vram_mb=4096, ram_mb=8192))
    assert not result.changed() and result.preset is preset


def test_tuning_is_idempotent() -> None:
    hardware = HardwareInfo(vram_mb=4096, ram_mb=8192)
    once = tune_preset(cycles_preset(**{DENOISING_USE_GPU: True}), hardware)
    twice = tune_preset(once.preset, hardware)
    assert once.changed() and not twice.changed()


def test_small_card_moves_denoising_to_cpu() -> None:
    result = tune_preset(cycles_preset(**{DENOISING_USE_GPU: True}), HardwareInfo(vram_mb=4096))
    assert result.preset.cycles[DENOISING_USE_GPU] is False
    assert "denoising on CPU (4 GB VRAM)" in result.notes


def test_an_8gb_card_needs_no_tuning() -> None:
    """Целевой ноутбук из спеки: подстройка не должна на нём ничего менять."""
    result = tune_preset(cycles_preset(**{DENOISING_USE_GPU: True}), HardwareInfo(vram_mb=8151, ram_mb=32189))
    assert not result.changed()


def test_persistent_data_follows_ram_not_vram() -> None:
    tight = tune_preset(cycles_preset(), HardwareInfo(ram_mb=8192))
    assert tight.preset.common[PERSISTENT_DATA] is False
    assert tight.notes == ["Persistent Data off (8 GB RAM)"]
    assert not tune_preset(cycles_preset(), HardwareInfo(ram_mb=32189)).changed()


def test_unknown_hardware_changes_nothing() -> None:
    preset = cycles_preset(**{DENOISING_USE_GPU: True})
    result = tune_preset(preset, HardwareInfo())
    assert not result.changed() and result.preset is preset


def test_eevee_scene_keeps_the_cycles_section_alone() -> None:
    """На EEVEE секция Cycles не применяется — обещать урезание там нечестно."""
    preset = cycles_preset(**{DENOISING_USE_GPU: True})
    result = tune_preset(preset, HardwareInfo(vram_mb=4096), engine="BLENDER_EEVEE")
    assert not result.changed()
    ram_only = tune_preset(preset, HardwareInfo(vram_mb=4096, ram_mb=8192), engine="BLENDER_EEVEE")
    assert list(ram_only.changes) == [PERSISTENT_DATA]  # Persistent Data не зависит от движка


def test_tuning_never_touches_the_rendered_image() -> None:
    """Подстройка не меняет то, что видно на кадре.

    ``texture_limit_render`` уменьшает разрешение текстур — ему место в лестнице
    ретраев после OOM, а не в тихой подстройке под железо.
    """
    image_changing = {"cycles.texture_limit_render", "render.use_simplify", "cycles.samples"}
    for vram_mb in (2048, 4096, 8151, 12288, 24576):
        result = tune_preset(cycles_preset(**{DENOISING_USE_GPU: True}), HardwareInfo(vram_mb=vram_mb, ram_mb=8192))
        assert not (set(result.changes) & image_changing), result.changes


def test_tuned_properties_stay_in_sync_with_the_oom_ladder() -> None:
    """Проактивная подстройка и ретрай после OOM обязаны крутить одни и те же ручки."""
    ladder = {path for _, step in OOM_RETRY_STEPS for path in step}
    result = tune_preset(cycles_preset(**{DENOISING_USE_GPU: True}), HardwareInfo(vram_mb=2048, ram_mb=8192))
    assert set(result.changes) <= ladder
