"""Smoke-тесты UI в offscreen-режиме.

Пробы Blender подменяются загрузчиками, которые отдают сохранённые фикстуры,
рендер — поддельным python-скриптом через FakePlanBuilder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from qt_helpers import FakePlanBuilder, wait_until

from brm.core import job_runner as job_runner_mod
from brm.core.capabilities import Capabilities, CapabilitiesError
from brm.core.frame_range import FrameRangeMode
from brm.core.project_probe import ProjectInfo, ProjectProbeError
from brm.core.queue import QueueStore
from brm.core.render_stats import FrameStat, RenderProgress
from brm.core.storage import AppSettings, SettingsStore
from brm.ui.log_view import LogView
from brm.ui.main_window import MainWindow
from brm.ui.progress_panel import ProgressPanel
from brm.ui.queue_view import frames_text
from brm.ui.settings_dialog import SettingsDialog
from brm.ui.settings_form import MODE_CUSTOM, MODE_PRESET, MODE_SKIP, SettingsForm
from brm.ui.sparkline import Sparkline

CAPS_FIXTURE = "capabilities_blender_5.0.1.json"
PROJECT_FIXTURE = "project_default_scene_5.0.1.json"


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


@pytest.fixture
def queue_store(tmp_path: Path) -> QueueStore:
    return QueueStore(tmp_path / "queue.json")


def make_window(store, *, caps_loader, project_loader, queue_store=None, fake_builder=None, monkeypatch=None):
    if fake_builder is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(job_runner_mod, "build_render_plan", fake_builder)
    return MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader, queue_store=queue_store)


def load_project(qapp, window: MainWindow, blend_file: Path) -> None:
    wait_until(qapp, lambda: window.capabilities is not None)
    window.open_project(str(blend_file))
    wait_until(qapp, lambda: window.project is not None)


# --- Blender и проект --------------------------------------------------------------


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
    assert "5.0.1" in window.blender_label.text() and "OPTIX" in window.blender_label.text()
    assert window.task_label.isHidden() and window.cancel_button.isHidden()


def test_failed_probe_shows_first_line_in_banner(qapp, configured_store: SettingsStore) -> None:
    def failing(blender_path: str, *, cancel):
        raise CapabilitiesError("Capabilities probe failed with exit code 1\nlast log line")

    window = MainWindow(configured_store, capabilities_loader=failing)
    wait_until(qapp, lambda: window.capabilities_error is not None)
    assert window.banner.message() == "Blender is not configured: Capabilities probe failed with exit code 1"
    assert "last log line" in window.render_button.toolTip()
    assert not window.render_button.isEnabled()


def test_unsupported_version_shows_banner(qapp, configured_store: SettingsStore) -> None:
    def old_blender(blender_path: str, *, cancel):
        return Capabilities(blender_version=(4, 1, 0), version_string="4.1.0", engines=["CYCLES"], blender_path=blender_path)

    window = MainWindow(configured_store, capabilities_loader=old_blender)
    wait_until(qapp, lambda: window.capabilities is not None)
    assert not window.banner.isHidden() and "not supported" in window.banner.message()


def test_stale_path_brings_banner_back(qapp, configured_store: SettingsStore, fake_blender: Path, caps_loader) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    wait_until(qapp, lambda: window.capabilities is not None)
    fake_blender.unlink()  # Blender «переустановили», путь протух
    window.refresh_blender_status()
    assert not window.banner.isHidden() and "not found" in window.render_button.toolTip()


def test_open_project_fills_panel_and_recent(qapp, configured_store: SettingsStore, caps_loader, project_loader, blend_file: Path) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    panel = window.project_panel
    assert panel.form.isEnabled()
    assert panel.scene_combo.currentText() == "Scene" and panel.view_layer_combo.currentText() == "ViewLayer"
    assert "Camera" in panel.camera_label.text()
    assert "250 frames (1..250)" in panel.frames_label.text()
    assert "Saved with Blender 5.0" in panel.summary_label.text() and "1920×1080" in panel.summary_label.text()
    assert panel.output_preview.text().endswith("####") and panel.output_preview.text().startswith(r"D:\out")
    job = panel.current_job()
    assert job.blend_path == str(blend_file) and job.scene == "Scene" and job.frame_range.mode is FrameRangeMode.FROM_FILE
    assert job.resume is True and job.min_frame_kb == 0 and job.chunk_size is None
    assert configured_store.load().recent_projects == [str(blend_file)]


def test_frame_mode_changes_update_summary(qapp, configured_store: SettingsStore, caps_loader, project_loader, blend_file: Path) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    panel = window.project_panel
    panel.mode_combo.setCurrentIndex(2)  # Single frame
    assert "1 frame" in panel.frames_label.text() and not panel.step_spin.isEnabled()
    panel.mode_combo.setCurrentIndex(3)  # Frame list
    panel.list_edit.setText("1,5,10..12")
    assert "5 frames (1..12)" in panel.frames_label.text()
    panel.list_edit.setText("1..x")
    assert "Cannot parse" in panel.frames_label.text()
    panel.mode_combo.setCurrentIndex(1)  # Manual
    panel.start_spin.setValue(10)
    panel.end_spin.setValue(19)
    panel.step_spin.setValue(5)
    assert "2 frames (10..15)" in panel.frames_label.text()
    panel.chunk_spin.setValue(20)
    panel.min_kb_spin.setValue(4)
    panel.resume_check.setChecked(False)
    job = panel.current_job()
    assert (job.chunk_size, job.min_frame_kb, job.resume) == (20, 4, False)


def test_project_probe_failure_is_reported(qapp, configured_store: SettingsStore, caps_loader, blend_file: Path) -> None:
    def failing(blender_path: str, blend_path: str, *, cancel):
        raise ProjectProbeError("Project probe failed with exit code 1")

    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=failing)
    wait_until(qapp, lambda: window.capabilities is not None)
    window.open_project(str(blend_file))
    wait_until(qapp, lambda: "Could not read" in window.project_panel.summary_label.text())
    assert window.project is None and not window.project_panel.form.isEnabled()


def test_open_project_requires_blender_and_existing_file(qapp, settings_path: Path, blend_file: Path, tmp_path: Path) -> None:
    window = MainWindow(SettingsStore(settings_path))
    window.open_project(str(blend_file))
    assert "Configure a working Blender first" in window.project_panel.summary_label.text()
    window.open_project(str(tmp_path / "missing.blend"))
    assert "File not found" in window.project_panel.summary_label.text()


# --- панели ---------------------------------------------------------------------------


def test_log_view_filters_and_copy(qapp) -> None:
    view = LogView()
    for line in ("Fra:1 Mem:10M", "[BRM] OK   scene = 'Scene'", "Error: boom", "[BRM] FAIL x: nope"):
        view.append_line(line)
    view.filter_combo.setCurrentIndex(1)  # [BRM] only
    assert view.text.toPlainText().splitlines() == ["[BRM] OK   scene = 'Scene'", "[BRM] FAIL x: nope"]
    view.filter_combo.setCurrentIndex(2)  # Errors
    assert view.text.toPlainText().splitlines() == ["Error: boom", "[BRM] FAIL x: nope"]
    view.set_command('"C:\\b.exe" -b "a.blend"')
    view.copy_command()
    assert qapp.clipboard().text() == '"C:\\b.exe" -b "a.blend"' and view.status_label.text() == "Command copied"


def test_progress_panel_states(qapp) -> None:
    panel = ProgressPanel()
    panel.set_running(10)
    progress = RenderProgress(frames_expected=list(range(1, 11)), frames_done=[1, 2], current_frame=3, sample=4, samples_total=16, mem_mb=360, peak_mb=512, engine="Cycles")
    progress.frame_stats = [FrameStat(frame=1, render_time_s=0.7, wall_time_s=2.0), FrameStat(frame=2, render_time_s=0.6, wall_time_s=2.0)]
    panel.update_progress(progress, elapsed_s=65, note="chunk 1 / 5")
    assert panel.frames_bar.value() == 2 and panel.samples_bar.maximum() == 16
    assert panel.status_label.text() == "Frame 2 / 10 · rendering 3 · sample 4 / 16 · chunk 1 / 5"
    details = panel.detail_label.text()
    assert "Elapsed 1m 05s" in details and "ETA 16 s" in details and "Peak 512 M" in details
    panel.set_finished("Failed", "error", hint="Out of memory")
    assert not panel.hint_label.isHidden()
    panel.set_idle()
    assert panel.hint_label.isHidden() and panel.sparkline.values() == []


def test_sparkline_paints_and_shows_tooltip(qapp) -> None:
    widget = Sparkline()
    widget.resize(200, 60)
    widget.set_values([(1, 0.5), (2, 1.5), (3, 1.0)])
    assert not widget.grab().isNull()
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(2, 30), QPointF(2, 30), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.mouseMoveEvent(event)
    assert widget.toolTip() == "Frame 1: 0.50 s"


def test_settings_form_modes_and_values(qapp, fixtures_dir: Path) -> None:
    from brm.core.preset_resolver import resolve_preset
    from brm.core.presets import load_presets

    caps = Capabilities.model_validate(json.loads((fixtures_dir / CAPS_FIXTURE).read_text(encoding="utf-8")))
    presets = load_presets(user_dir=fixtures_dir / "none")
    form = SettingsForm()
    chosen: list[str] = []
    form.preset_changed.connect(chosen.append)
    form.set_presets(presets, "Balanced")
    assert form.current_preset_name() == "Balanced" and chosen == []
    form.set_engine("CYCLES")
    form.show_resolved(resolve_preset(presets[2], caps, "CYCLES"))
    rows = form.rows
    assert rows["samples"].value() == 1024 and rows["denoise"].value() is True and rows["format"].value() == "PNG"
    rows["samples"].set_mode(MODE_CUSTOM)
    rows["samples"].set_value(64)
    rows["persistent"].set_mode(MODE_SKIP)
    assert form.custom_values() == {"cycles.samples": 64} and form.untouched_paths() == {"render.use_persistent_data"}
    rows["samples"].set_mode(MODE_PRESET)
    assert rows["samples"].value() == 1024
    form.set_engine("BLENDER_EEVEE")
    form.show_resolved(resolve_preset(presets[2], caps, "BLENDER_EEVEE"))
    assert rows["samples"].value() == 64 and rows["threshold"].note.text() == "Cycles only"
    form.preset_combo.setCurrentIndex(0)
    assert chosen == ["Draft"]


def test_queue_frames_text() -> None:
    from brm.core.frame_range import FrameRange

    assert frames_text(FrameRange()) == "from file"
    assert frames_text(FrameRange(mode=FrameRangeMode.FROM_FILE, step=4)) == "from file, step 4"
    assert frames_text(FrameRange(mode=FrameRangeMode.MANUAL, start=1, end=9, step=2)) == "1..9 step 2"
    assert frames_text(FrameRange(mode=FrameRangeMode.SINGLE, frame=7)) == "7"
    assert frames_text(FrameRange(mode=FrameRangeMode.LIST, frames_text="1,5")) == "1,5"


# --- рендер через окно --------------------------------------------------------------------


def test_render_button_requires_project(qapp, configured_store: SettingsStore, caps_loader) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    wait_until(qapp, lambda: window.capabilities is not None)
    window.start_render()
    assert "Load a project first" in window.log_view.status_label.text() and not window.runner.is_running()


def test_render_job_gets_preset_overrides(qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out"), last_preset="Draft"))
    captured: list = []

    def failing_builder(job, caps, settings, project, *, tmp_dir, frames_override=None):
        captured.append(job)
        raise ValueError("stop here")

    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=failing_builder, monkeypatch=monkeypatch)
    load_project(qapp, window, blend_file)
    assert window.settings_form.current_preset_name() == "Draft" and window.resolved_preset.engine == "BLENDER_EEVEE"
    assert window.settings_form.rows["samples"].value() == 16  # Draft для EEVEE
    window.settings_form.rows["resolution"].set_mode(MODE_CUSTOM)
    window.settings_form.rows["resolution"].set_value(25)
    window.settings_form.rows["persistent"].set_mode(MODE_SKIP)

    window.start_render()
    job = captured[-1]
    assert job.preset == "Draft" and job.engine is None and job.file_format == "JPEG"
    assert job.overrides["eevee.taa_render_samples"] == 16 and job.overrides["render.resolution_percentage"] == 25
    assert "render.use_persistent_data" not in job.overrides and "cycles.samples" not in job.overrides
    assert "stop here" in window.log_view.status_label.text()

    # Режимы полей переживают смену пресета: «не трогать» — выбор пользователя, а не пресета.
    window.settings_form.preset_combo.setCurrentIndex(4)  # Heavy Scene: chunk 20 из пресета
    window.start_render()
    assert captured[-1].chunk_size == 20
    assert "render.use_persistent_data" not in captured[-1].overrides

    window.settings_form.rows["persistent"].set_mode(MODE_PRESET)
    window.start_render()
    assert captured[-1].overrides["render.use_persistent_data"] is False  # Heavy Scene выключает
    window.project_panel.chunk_spin.setValue(7)
    window.start_render()
    assert captured[-1].chunk_size == 7


def test_start_render_reports_unstartable_blender(qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch) -> None:
    """Поддельный blender.exe запуститься не может: один повтор, статус failed, кнопки возвращаются."""
    from brm.ui import main_window as main_window_mod

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    monkeypatch.setattr(main_window_mod, "tmp_dir", lambda: tmp_path / "brm_tmp")
    window = MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)

    window.start_render()
    assert window.log_view.command().startswith(f'"{fake_blender}"') and " -S Scene " in window.log_view.command()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=30)
    assert window.runner.status == "failed"
    assert "Failed" in window.log_view.status_label.text()
    assert window.render_button.isEnabled() and not window.stop_button.isEnabled() and not window.pause_button.isEnabled()
    lines = window.log_view.lines()
    assert any("could not start" in line for line in lines) and any(line.startswith("[BRM] retry 1/1") for line in lines)
    assert len(window.runner.plans) == 2 and window.runner.plans[0].log_path.is_file()


def test_pause_after_frame_and_resume(qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3, 4], delay=0.4)
    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch)
    load_project(qapp, window, blend_file)
    window.project_panel.chunk_spin.setValue(2)

    window.start_render()
    assert window.pause_button.isEnabled() and window.pause_button.text() == "Pause"
    wait_until(qapp, lambda: window.runner.tracker is not None and window.runner.tracker.progress.frames_done_count >= 1, timeout=20)
    window.pause_or_resume()
    assert not window.pause_button.isEnabled()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=30)
    assert window.runner.status == "paused"
    assert window.pause_button.text() == "Resume" and window.pause_button.isEnabled() and window.render_button.isEnabled()
    assert "Paused after" in window.progress_panel.status_label.text()

    window.pause_or_resume()  # Resume
    assert not window.render_button.isEnabled()
    wait_until(qapp, lambda: window.runner.status == "success", timeout=30)
    assert window.runner.tracker.progress.frames_done == [1, 2, 3, 4]  # сквозной прогресс
    assert "Finished" in window.progress_panel.status_label.text() and window.pause_button.text() == "Pause"
    assert window.progress_panel.sparkline.values() == [(f, 0.1) for f in [1, 2, 3, 4]]
    stats = json.loads(window.runner.plans[0].stats_path.read_text(encoding="utf-8"))
    assert stats["frames_done"] == [1, 2, 3, 4] and stats["status"] == "success"
    assert builder.chunk_calls[0] == [1, 2]


def test_queue_runs_items_sequentially_and_persists(qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, queue_store: QueueStore, monkeypatch) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2], delay=0.05)
    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader, queue_store=queue_store, fake_builder=builder, monkeypatch=monkeypatch)
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)

    window.add_current_to_queue()
    window.settings_form.preset_combo.setCurrentIndex(0)  # Draft
    window.add_current_to_queue()
    assert window.queue_view.table.rowCount() == 2
    saved = QueueStore(queue_store.path).load()
    assert [item.job.preset for item in saved.items] == ["Balanced", "Draft"]
    assert "2 pending" in window.queue_view.status_label.text()

    window.run_queue()
    assert window.queue_view.run_button.text() == "Queue running…"
    wait_until(qapp, lambda: not window._queue_running and window.runner.status is not None, timeout=40)
    statuses = [item.status for item in window.queue.items]
    assert statuses == ["done", "done"]
    assert all(item.frames_done == 2 and item.frames_total == 2 for item in window.queue.items)
    assert "Queue finished" in window.log_view.status_label.text()
    assert len(builder.chunk_calls) == 2
    assert QueueStore(queue_store.path).load().items[0].status == "done"

    window.queue_view.table.selectRow(0)
    window.remove_queue_items(window.queue_view.selected_ids())
    assert window.queue_view.table.rowCount() == 1
    window.clear_finished_queue()
    assert window.queue_view.table.rowCount() == 0 and "empty" in window.queue_view.status_label.text()


def test_queue_restores_interrupted_items(qapp, settings_path: Path, fake_blender: Path, caps_loader, tmp_path: Path, queue_store: QueueStore) -> None:
    from brm.core.models import RenderJob
    from brm.core.project_probe import SceneInfo
    from brm.core.queue import RenderQueue

    queue = RenderQueue()
    item = queue.add(RenderJob(blend_path="x.blend"), ProjectInfo(file_path="x.blend", scenes=[SceneInfo(name="S")]))
    item.status = "running"
    queue_store.save(queue)
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender)))
    window = MainWindow(store, capabilities_loader=caps_loader, queue_store=queue_store)
    assert window.queue.items[0].status == "pending"
    assert window.queue_view.table.item(0, 4).text() == "pending"


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
    assert dialog.theme_combo.currentData() == "dark"
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
    result = dialog.result_settings()
    assert result.blender_path == str(fake_blender) and result.default_output_dir == r"D:\out"
    assert result.ffmpeg_path is None and result.theme == "light"
