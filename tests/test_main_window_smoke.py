"""Smoke-тесты UI в offscreen-режиме.

Пробы Blender подменяются загрузчиками, которые отдают сохранённые фикстуры,
поэтому настоящий Blender не нужен, а фоновые задачи отрабатывают за миллисекунды.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from brm.core.capabilities import Capabilities, CapabilitiesError
from brm.core.frame_range import FrameRangeMode
from brm.core.project_probe import ProjectInfo, ProjectProbeError
from brm.core.storage import AppSettings, SettingsStore
from brm.ui.main_window import MainWindow
from brm.ui.settings_dialog import SettingsDialog

CAPS_FIXTURE = "capabilities_blender_5.0.1.json"
PROJECT_FIXTURE = "project_default_scene_5.0.1.json"


def wait_until(qapp, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Крутит цикл событий, пока условие не выполнится: сигналы из потоков приходят через очередь."""
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        time.sleep(0.01)
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for the UI state")


@pytest.fixture
def caps_loader(fixtures_dir: Path):
    data = json.loads((fixtures_dir / CAPS_FIXTURE).read_text(encoding="utf-8"))

    def loader(blender_path: str, *, cancel) -> Capabilities:
        caps = Capabilities.model_validate(data)
        caps.blender_path = blender_path
        return caps

    return loader


@pytest.fixture
def project_loader(fixtures_dir: Path):
    data = json.loads((fixtures_dir / PROJECT_FIXTURE).read_text(encoding="utf-8"))

    def loader(blender_path: str, blend_path: str, *, cancel) -> ProjectInfo:
        info = ProjectInfo.model_validate(data)
        info.file_path = blend_path
        return info

    return loader


@pytest.fixture
def configured_store(settings_path: Path, fake_blender: Path) -> SettingsStore:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=r"D:\out"))
    return store


@pytest.fixture
def blend_file(tmp_path: Path) -> Path:
    blend = tmp_path / "shots" / "Пещера v3.blend"
    blend.parent.mkdir()
    blend.write_bytes(b"BLENDER")
    return blend


def test_without_blender_banner_visible_and_render_disabled(qapp, settings_path: Path) -> None:
    window = MainWindow(SettingsStore(settings_path))
    assert not window.banner.isHidden()
    assert "Blender is not configured" in window.banner.message()
    assert not window.render_button.isEnabled()
    assert "blender.exe" in window.render_button.toolTip()


def test_valid_blender_probe_enables_render(qapp, configured_store: SettingsStore, caps_loader) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    assert not window.render_button.isEnabled()  # пока идёт проба
    wait_until(qapp, lambda: window.capabilities is not None)
    assert window.banner.isHidden()
    assert window.render_button.isEnabled()
    assert "5.0.1" in window.blender_label.text()
    assert "OPTIX" in window.blender_label.text()
    assert window.task_label.isHidden() and window.cancel_button.isHidden()


def test_failed_probe_shows_first_line_in_banner(qapp, configured_store: SettingsStore) -> None:
    def failing(blender_path: str, *, cancel):
        raise CapabilitiesError("Capabilities probe failed with exit code 1\nlast log line")

    window = MainWindow(configured_store, capabilities_loader=failing)
    wait_until(qapp, lambda: window.capabilities_error is not None)
    assert not window.banner.isHidden()
    assert window.banner.message() == "Blender is not configured: Capabilities probe failed with exit code 1"
    assert "last log line" in window.render_button.toolTip()
    assert not window.render_button.isEnabled()


def test_unsupported_version_shows_banner(qapp, configured_store: SettingsStore) -> None:
    def old_blender(blender_path: str, *, cancel):
        return Capabilities(blender_version=(4, 1, 0), version_string="4.1.0", engines=["CYCLES"], blender_path=blender_path)

    window = MainWindow(configured_store, capabilities_loader=old_blender)
    wait_until(qapp, lambda: window.capabilities is not None)
    assert not window.banner.isHidden()
    assert "not supported" in window.banner.message()
    assert not window.render_button.isEnabled()


def test_stale_path_brings_banner_back(qapp, configured_store: SettingsStore, fake_blender: Path, caps_loader) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    wait_until(qapp, lambda: window.capabilities is not None)
    assert window.banner.isHidden()

    fake_blender.unlink()  # Blender «переустановили», путь протух
    window.refresh_blender_status()

    assert not window.banner.isHidden()
    assert not window.render_button.isEnabled()
    assert "not found" in window.render_button.toolTip()


def test_open_project_fills_panel_and_recent(qapp, configured_store: SettingsStore, caps_loader, project_loader, blend_file: Path) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=project_loader)
    wait_until(qapp, lambda: window.capabilities is not None)

    window.open_project(str(blend_file))
    assert "Reading" in window.project_panel.summary_label.text()
    wait_until(qapp, lambda: window.project is not None)

    panel = window.project_panel
    assert panel.form.isEnabled()
    assert panel.scene_combo.currentText() == "Scene"
    assert panel.view_layer_combo.currentText() == "ViewLayer"
    assert "Camera" in panel.camera_label.text()
    assert "250 frames (1..250)" in panel.frames_label.text()
    assert "Saved with Blender 5.0" in panel.summary_label.text()
    assert "1920×1080" in panel.summary_label.text()
    assert panel.warnings_label.isHidden()
    assert panel.output_preview.text().endswith("####")
    assert "Пещера v3" in panel.output_preview.text() and panel.output_preview.text().startswith(r"D:\out")

    job = panel.current_job()
    assert job is not None
    assert job.blend_path == str(blend_file) and job.scene == "Scene" and job.view_layer == "ViewLayer"
    assert job.frame_range.mode is FrameRangeMode.FROM_FILE and job.frame_range.step == 1

    assert configured_store.load().recent_projects == [str(blend_file)]
    assert window.project_panel.recent_button.isEnabled()


def test_frame_mode_changes_update_summary(qapp, configured_store: SettingsStore, caps_loader, project_loader, blend_file: Path) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=project_loader)
    wait_until(qapp, lambda: window.capabilities is not None)
    window.open_project(str(blend_file))
    wait_until(qapp, lambda: window.project is not None)
    panel = window.project_panel

    panel.mode_combo.setCurrentIndex(2)  # Single frame
    assert "1 frame" in panel.frames_label.text()
    assert not panel.step_spin.isEnabled()

    panel.mode_combo.setCurrentIndex(3)  # Frame list
    panel.list_edit.setText("1,5,10..12")
    assert "5 frames (1..12)" in panel.frames_label.text()
    assert panel.current_job().frame_range.frames_text == "1,5,10..12"

    panel.list_edit.setText("1..x")
    assert "Cannot parse" in panel.frames_label.text()

    panel.mode_combo.setCurrentIndex(1)  # Manual
    panel.start_spin.setValue(10)
    panel.end_spin.setValue(19)
    panel.step_spin.setValue(5)
    assert "2 frames (10..15)" in panel.frames_label.text()


def test_project_probe_failure_is_reported(qapp, configured_store: SettingsStore, caps_loader, blend_file: Path) -> None:
    def failing(blender_path: str, blend_path: str, *, cancel):
        raise ProjectProbeError("Project probe failed with exit code 1")

    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=failing)
    wait_until(qapp, lambda: window.capabilities is not None)
    window.open_project(str(blend_file))
    wait_until(qapp, lambda: "Could not read" in window.project_panel.summary_label.text())
    assert window.project is None
    assert not window.project_panel.form.isEnabled()
    assert configured_store.load().recent_projects == []


def test_open_project_requires_blender_and_existing_file(qapp, settings_path: Path, blend_file: Path, tmp_path: Path) -> None:
    window = MainWindow(SettingsStore(settings_path))
    window.open_project(str(blend_file))
    assert "Configure a working Blender first" in window.project_panel.summary_label.text()
    window.open_project(str(tmp_path / "missing.blend"))
    assert "File not found" in window.project_panel.summary_label.text()


def test_author_credit_is_shown(qapp, settings_path: Path) -> None:
    window = MainWindow(SettingsStore(settings_path))
    assert window.credit_label.text() == "Made by Pavel Postnikov"


def test_settings_dialog_ok_follows_validation(qapp, fake_blender: Path) -> None:
    dialog = SettingsDialog(AppSettings())
    assert not dialog.ok_button.isEnabled()

    dialog.blender_edit.setText(str(fake_blender))
    assert dialog.ok_button.isEnabled()

    dialog.output_edit.setText(r"D:\out")
    dialog.ffmpeg_edit.setText("   ")
    result = dialog.result_settings()
    assert result.blender_path == str(fake_blender)
    assert result.default_output_dir == r"D:\out"
    assert result.ffmpeg_path is None
