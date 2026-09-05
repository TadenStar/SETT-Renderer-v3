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
from brm.core.preview import describe_unpreviewable
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
from brm.core.frame_chart import ROLE_CURRENT
from brm.ui.frame_chart import FrameChart

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
    assert panel.hint_label.isHidden() and panel.chart.series() == []


def current_points(chart: FrameChart) -> list[tuple[int, float]]:
    """Точки синей линии — того прогона, который на графике считается текущим."""
    current = [s for s in chart.series() if s.role == ROLE_CURRENT]
    return list(current[0].points) if current else []


def test_frame_chart_paints_and_shows_tooltip(qapp) -> None:
    from brm.core.frame_chart import ROLE_RECENT, ROLE_REFERENCE, ChartSeries

    widget = FrameChart()
    widget.resize(200, 90)
    widget.set_series([
        ChartSeries("Current render", ((1, 0.5), (2, 1.5), (3, 1.0)), ROLE_CURRENT),
        ChartSeries("Yesterday", ((1, 2.0), (2, 2.5), (3, 2.2)), ROLE_RECENT, age=0),
        ChartSeries("Gold", ((1, 1.8), (2, 1.9), (3, 1.7)), ROLE_REFERENCE),
    ])
    assert not widget.grab().isNull()
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(2, 88), QPointF(2, 88), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    widget.mouseMoveEvent(event)
    assert "frame 1" in widget.toolTip()


def test_frame_chart_colors_fade_with_age(qapp) -> None:
    """Чем старше прогон, тем темнее его линия — как просил Павел."""
    from brm.core.frame_chart import ROLE_RECENT, ChartSeries

    widget = FrameChart()
    shades = [widget.color_for(ChartSeries("x", ((1, 1.0),), ROLE_RECENT, age=age)) for age in range(5)]
    lightness = [c.lightness() for c in shades]
    assert lightness == sorted(lightness, reverse=True), lightness
    assert all(a.alpha() >= b.alpha() for a, b in zip(shades, shades[1:], strict=False))


def test_settings_form_modes_and_values(qapp, fixtures_dir: Path) -> None:
    from brm.core.preset_resolver import resolve_preset
    from brm.core.presets import load_presets

    caps = Capabilities.model_validate(json.loads((fixtures_dir / CAPS_FIXTURE).read_text(encoding="utf-8")))
    presets = load_presets(user_dir=fixtures_dir / "none")
    form = SettingsForm()
    chosen: list[str] = []
    form.preset_changed.connect(chosen.append)
    form.set_presets(presets, "Super")
    assert form.current_preset_name() == "Super" and chosen == []
    form.set_engine("CYCLES")
    form.show_resolved(resolve_preset(presets[1], caps, "CYCLES"))  # Super
    rows = form.rows
    assert rows["samples"].value() == 512 and rows["denoise"].value() is True
    rows["samples"].set_mode(MODE_CUSTOM)
    rows["samples"].set_value(64)
    rows["persistent"].set_mode(MODE_SKIP)
    assert form.custom_values() == {"cycles.samples": 64} and form.untouched_paths() == {"render.use_persistent_data"}
    rows["samples"].set_mode(MODE_PRESET)
    assert rows["samples"].value() == 512
    form.set_engine("BLENDER_EEVEE")
    form.show_resolved(resolve_preset(presets[1], caps, "BLENDER_EEVEE"))  # Super
    assert rows["samples"].value() == 192 and rows["threshold"].note.text() == "Cycles only"
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
    window.settings_form.preset_combo.setCurrentIndex(1)  # Super
    window.start_render()
    assert "render.use_persistent_data" not in captured[-1].overrides

    window.settings_form.rows["persistent"].set_mode(MODE_PRESET)
    window.start_render()
    assert captured[-1].overrides["render.use_persistent_data"] is True  # Super его включает
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
    assert current_points(window.progress_panel.chart) == [(f, 0.1) for f in [1, 2, 3, 4]]
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

    window.settings_form.preset_combo.setCurrentIndex(1)  # Super
    window.add_current_to_queue()
    window.settings_form.preset_combo.setCurrentIndex(0)  # Draft
    window.add_current_to_queue()
    assert window.queue_view.table.rowCount() == 2
    saved = QueueStore(queue_store.path).load()
    assert [item.job.preset for item in saved.items] == ["Super", "Draft"]
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
    assert window.credit_label.text() == "Made by Pavel Postnikov · Build 3001"


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


# --- видео, уведомления, выключение (M6) ---------------------------------------------


@pytest.fixture
def fake_ffmpeg(tmp_path: Path) -> Path:
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    return exe


def rendered_sequence(directory: Path, frames: int = 3) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for frame in range(1, frames + 1):
        (directory / f"{frame:04d}.png").write_bytes(b"x" * 200)
    return directory / "####"


def test_video_panel_disabled_without_ffmpeg(qapp, configured_store: SettingsStore, caps_loader) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    panel = window.video_panel
    assert not panel.build_button.isEnabled() and not panel.preset_combo.isEnabled()
    assert "ffmpeg.exe is not set" in panel.status_label.text()
    assert [panel.preset_combo.itemText(i) for i in range(panel.preset_combo.count())][0] == "H.264"
    window.build_video()
    assert "ffmpeg" in panel.status_label.text()
    assert not window.video_process.is_running()


def test_video_build_composes_argv_and_runs(qapp, settings_path: Path, fake_blender: Path, fake_ffmpeg: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), ffmpeg_path=str(fake_ffmpeg), default_output_dir=str(tmp_path / "out")))
    window = MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    assert window.video_panel.build_button.isEnabled()

    pattern = rendered_sequence(tmp_path / "out" / "Пещера v3" / "Scene", frames=4)
    assert window.build_video(str(pattern)) is True
    argv = window.video_process.argv
    assert Path(argv[0]) == fake_ffmpeg
    assert argv[argv.index("-framerate") + 1] == "24"          # fps сцены из фикстуры
    assert argv[argv.index("-i") + 1].endswith("%04d.png")
    assert argv[argv.index("-crf") + 1] == "17"                # пресет H.264
    assert argv[-1].endswith("Scene_h264.mp4")
    assert window.video_process.progress.total_frames == 4
    wait_until(qapp, lambda: window.video_process.status is not None, timeout=20)
    assert window.video_process.status == "crashed"            # поддельный ffmpeg не запускается
    assert "failed" in window.video_panel.status_label.text()


def test_video_reports_missing_sequence(qapp, settings_path: Path, fake_blender: Path, fake_ffmpeg: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), ffmpeg_path=str(fake_ffmpeg), default_output_dir=str(tmp_path / "out")))
    window = MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert window.build_video(str(empty / "####")) is False
    assert "No rendered frames" in window.video_panel.status_label.text()
    assert window.build_video(str(tmp_path / "gone" / "####")) is False
    assert "does not exist" in window.video_panel.status_label.text()


def test_auto_build_runs_before_the_queue_moves_on(qapp, settings_path: Path, fake_blender: Path, fake_ffmpeg: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, queue_store: QueueStore, monkeypatch) -> None:
    """Видео собирается до перехода к следующей задаче и до автовыключения."""
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), ffmpeg_path=str(fake_ffmpeg), default_output_dir=str(tmp_path / "out"), shutdown_after_queue=True))
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2], delay=0.05)
    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader, queue_store=queue_store, fake_builder=builder, monkeypatch=monkeypatch)
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)
    window.video_panel.set_auto_build(True)

    order: list[str] = []
    real_build = window.build_video
    monkeypatch.setattr(window, "build_video", lambda path=None: (order.append("video"), real_build(path))[1])
    monkeypatch.setattr(window, "maybe_shutdown", lambda: order.append("shutdown"))

    window.add_current_to_queue()
    window.run_queue()
    wait_until(qapp, lambda: not window._queue_running and window.video_process.status is not None, timeout=60)
    assert order == ["video", "shutdown"]  # видео строго перед выключением
    assert window.queue.items[0].status == "done"


def test_shutdown_banner_and_cancel(qapp, configured_store: SettingsStore, caps_loader, monkeypatch) -> None:
    from brm.core import system_actions
    from brm.ui import main_window as main_window_mod

    calls: list[str] = []
    monkeypatch.setattr(main_window_mod, "schedule_shutdown", lambda delay=60: (calls.append(f"schedule {delay}"), system_actions.ActionResult(True, "ok"))[1])
    monkeypatch.setattr(main_window_mod, "cancel_shutdown", lambda: (calls.append("cancel"), system_actions.ActionResult(True, "Shutdown cancelled"))[1])

    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    assert window.shutdown_banner.isHidden()
    window.maybe_shutdown()  # выключение выключено в настройках
    assert calls == [] and window.shutdown_banner.isHidden()

    window.settings = window.settings.model_copy(update={"shutdown_after_queue": True})
    window.maybe_shutdown()
    assert calls == ["schedule 60"]
    assert not window.shutdown_banner.isHidden() and "shut down in 60 s" in window.shutdown_banner.message()
    window.maybe_shutdown()  # повторно не планируем
    assert calls == ["schedule 60"]

    window.cancel_shutdown()
    assert calls[-1] == "cancel" and window.shutdown_banner.isHidden()
    assert any("Shutdown cancelled" in line for line in window.log_view.lines())


def test_shutdown_failure_is_reported_not_raised(qapp, configured_store: SettingsStore, caps_loader, monkeypatch) -> None:
    from brm.core import system_actions
    from brm.ui import main_window as main_window_mod

    monkeypatch.setattr(main_window_mod, "schedule_shutdown", lambda delay=60: system_actions.ActionResult(False, "Access is denied."))
    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    window.settings = window.settings.model_copy(update={"shutdown_after_queue": True})
    window.maybe_shutdown()
    assert window.shutdown_banner.isHidden()
    assert any("SKIP shutdown: Access is denied." in line for line in window.log_view.lines())


def test_notifier_survives_missing_tray(qapp) -> None:
    from brm.ui.notifications import Notifier

    notifier = Notifier()
    if notifier.available:
        assert notifier.notify("t", "m") is True
        notifier.enabled = False
        assert notifier.notify("t", "m") is False
    else:
        assert notifier.notify("t", "m") is False
    notifier.hide()


def test_video_choice_is_saved_on_close(qapp, settings_path: Path, fake_blender: Path, fake_ffmpeg: Path, caps_loader) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), ffmpeg_path=str(fake_ffmpeg)))
    window = MainWindow(store, capabilities_loader=caps_loader)
    window.video_panel.preset_combo.setCurrentIndex(1)  # ProRes
    window.video_panel.set_auto_build(True)
    window.close()
    saved = store.load()
    assert saved.last_video_preset == "ProRes 422 HQ" and saved.auto_build_video is True


# --- экспертная форма и режимы отображения (M7) -----------------------------------


def test_display_modes_switch_which_form_feeds_overrides(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path
) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out"), last_preset="Super"))
    window = MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)
    form = window.settings_form

    from brm.ui.settings_form import VIEW_EXPERT, VIEW_PRESET_ONLY, VIEW_SIMPLE

    assert form.display_mode() == VIEW_SIMPLE  # дефолт не меняется — совместимость с M4-M6
    assert form.expert_form.field_count() > 150  # capabilities дошли до экспертной формы

    # Сцена из фикстуры по умолчанию на EEVEE — path строки "samples" ведёт туда.
    form.rows["samples"].set_mode(MODE_CUSTOM)
    form.rows["samples"].set_value(42)
    assert form.custom_values() == {"eevee.taa_render_samples": 42}

    # Expert больше не страница в панели: он живёт в своём окне, а список
    # режимов на это время блокируется, чтобы не врал о том, откуда значения.
    form.set_display_mode(VIEW_EXPERT)
    assert form.display_mode() == VIEW_EXPERT and not form.view_combo.isEnabled()
    assert form.custom_values() == {}  # экспертная форма ещё ничего не трогала

    form.expert_form.rows["eevee.taa_render_samples"].set_mode(MODE_CUSTOM)
    form.expert_form.rows["eevee.taa_render_samples"].set_value(16)
    assert form.custom_values() == {"eevee.taa_render_samples": 16}
    assert form.untouched_paths() == set()

    form.set_display_mode(VIEW_PRESET_ONLY)
    assert isinstance(form.stack.currentWidget(), type(form.stack.widget(0)))
    assert form.custom_values() == {} and form.untouched_paths() == set()

    # Simple-режим со своими значениями по-прежнему доступен после переключений.
    form.set_display_mode(VIEW_SIMPLE)
    assert form.custom_values() == {"eevee.taa_render_samples": 42}


def test_expert_mode_overrides_reach_the_composed_job(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path
) -> None:
    from brm.ui.settings_form import VIEW_EXPERT

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out"), last_preset="Super"))
    window = MainWindow(store, capabilities_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)

    window.settings_form.set_display_mode(VIEW_EXPERT)
    row = window.settings_form.expert_form.rows["view_settings.view_transform"]
    row.set_mode(MODE_CUSTOM)
    row.set_value("Standard")

    job = window.compose_job()
    assert job.overrides["view_settings.view_transform"] == "Standard"
    assert "cycles.samples" not in job.overrides  # simple-строки не в игре в Expert-режиме


# --- история (M7) --------------------------------------------------------------------


def test_history_is_recorded_after_a_finished_render(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from brm.core.history import HistoryStore

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    history_store = HistoryStore(tmp_path / "history.db")
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2], delay=0.02)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )
    window.history_store = history_store
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)

    window.start_render()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)

    entries = history_store.list_entries()
    assert len(entries) == 1
    assert entries[0].status == "success" and entries[0].frames_done == 2 and entries[0].preset == "Draft"
    assert entries[0].scene == "Scene"


def test_history_dialog_shows_entries_and_chart(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from brm.core.history import HistoryStore

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    history_store = HistoryStore(tmp_path / "history.db")
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3], delay=0.02)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )
    window.history_store = history_store
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)
    window.start_render()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)

    window.show_history()
    assert window._history_dialog is not None
    view = window._history_dialog.view
    assert view.table.rowCount() == 1
    assert view.count_label.text() == "1 render(s)"

    view.table.selectRow(0)
    entry = view.selected_entry()
    assert entry is not None and entry.frames_done == 3
    # fake_render_script печатает фиксированный "Time: 00:00.10" независимо от delay.
    assert current_points(view.chart) == [(f, 0.1) for f in (1, 2, 3)]
    assert "Scene" in view.chart_label.text()

    # Диалог уже открыт — новая запись в истории должна дойти до таблицы без повторного show_history().
    window.project_panel.mode_combo.setCurrentIndex(2)  # Single frame
    window.start_render()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)
    assert view.table.rowCount() == 2


def test_history_recording_never_crashes_the_finish_handler(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    builder = FakePlanBuilder(tmp_path, base_frames=[1], delay=0.02)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )

    class ExplodingHistoryStore:
        def record_from_stats_file(self, _path):
            raise RuntimeError("disk on fire")

    window.history_store = ExplodingHistoryStore()
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)
    window.start_render()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)

    assert "Finished" in window.log_view.status_label.text()
    assert any("SKIP history: disk on fire" in line for line in window.log_view.lines())


# --- онбординг первого запуска (M8) ------------------------------------------------


def test_onboarding_shown_once_and_flag_set_on_accept(qapp, settings_path: Path, fake_blender: Path, caps_loader) -> None:
    from PySide6.QtCore import QTimer

    from brm.ui import main_window as main_window_mod
    from brm.ui.onboarding_dialog import OnboardingDialog

    window = MainWindow(SettingsStore(settings_path))
    assert not SettingsStore(settings_path).load().onboarding_seen

    shown: list[OnboardingDialog] = []
    real_init = OnboardingDialog.__init__

    def spy_init(self, settings, parent=None):
        real_init(self, settings, parent)
        self.blender_edit.setText(str(fake_blender))
        shown.append(self)
        QTimer.singleShot(0, self.accept)  # закрываем модальный цикл уже после его старта

    main_window_mod.OnboardingDialog.__init__ = spy_init
    try:
        window.maybe_show_onboarding()
    finally:
        main_window_mod.OnboardingDialog.__init__ = real_init

    assert len(shown) == 1
    assert window.settings.onboarding_seen is True
    assert window.settings.blender_path == str(fake_blender)
    assert SettingsStore(settings_path).load().onboarding_seen is True

    # Второй вызов — диалог больше не показывается.
    window.maybe_show_onboarding()
    assert len(shown) == 1


def test_onboarding_flag_set_even_when_cancelled(qapp, settings_path: Path) -> None:
    from PySide6.QtCore import QTimer

    from brm.ui import main_window as main_window_mod
    from brm.ui.onboarding_dialog import OnboardingDialog

    window = MainWindow(SettingsStore(settings_path))
    real_init = OnboardingDialog.__init__

    def spy_init(self, settings, parent=None):
        real_init(self, settings, parent)
        QTimer.singleShot(0, self.reject)  # пользователь закрыл диалог, ничего не заполнив

    main_window_mod.OnboardingDialog.__init__ = spy_init
    try:
        window.maybe_show_onboarding()
    finally:
        main_window_mod.OnboardingDialog.__init__ = real_init

    assert window.settings.onboarding_seen is True
    assert window.settings.blender_path is None  # путь не тронут — Cancel не сохраняет поля


def test_onboarding_skipped_when_already_seen(qapp, settings_path: Path) -> None:
    from brm.ui import main_window as main_window_mod

    store = SettingsStore(settings_path)
    store.save(AppSettings(onboarding_seen=True))
    window = MainWindow(store)

    calls = []
    main_window_mod.OnboardingDialog = type("Spy", (), {"__init__": lambda self, *a, **k: calls.append(1)})
    try:
        window.maybe_show_onboarding()
    finally:
        from brm.ui.onboarding_dialog import OnboardingDialog
        main_window_mod.OnboardingDialog = OnboardingDialog

    assert calls == []


# --- превью последнего кадра -------------------------------------------------------


def _write_png(path: Path, width: int = 64, height: int = 36) -> Path:
    """Настоящий PNG: поддельный рендер пишет заглушки, а Qt нужен читаемый файл."""
    from PySide6.QtGui import QColor, QPixmap

    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#404040"))
    assert pixmap.save(str(path), "PNG")
    return path


def test_preview_window_reports_size_missing_file_and_exr(qapp, tmp_path: Path) -> None:
    from brm.ui.preview_window import PreviewWindow

    window = PreviewWindow()
    frame = _write_png(tmp_path / "0001.png", 80, 45)

    assert window.show_frame(frame, note="last frame on disk")
    assert window.image.has_image()
    assert "0001.png" in window.caption.text() and "80×45" in window.caption.text()
    assert "last frame on disk" in window.caption.text()

    assert not window.show_frame(tmp_path / "gone.png")
    assert not window.image.has_image() and "gone" in window.caption.text()

    window.show_message(describe_unpreviewable(tmp_path / "0002.exr"), "warning")
    assert not window.image.has_image() and "OpenEXR" in window.caption.text()

    window.clear()
    assert window.current_path is None and not window.image.has_image()


def test_preview_follows_saved_frames_only_while_open_and_following(
    qapp, configured_store: SettingsStore, caps_loader, tmp_path: Path
) -> None:
    from brm.core.log_parser import KIND_SAVED, LogEvent

    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    first = _write_png(tmp_path / "frames" / "0001.png")
    second = _write_png(tmp_path / "frames" / "0002.png")
    saved = lambda path: LogEvent(kind=KIND_SAVED, raw="", saved_path=str(path))  # noqa: E731

    window._on_render_event(saved(first))  # окно не открыто — просто ничего не делаем
    assert window._preview_window is None

    window.show_preview()
    preview = window._preview_window
    assert preview is not None and not preview.isHidden()

    window._on_render_event(saved(first))
    assert preview.current_path == first and preview.image.has_image()

    preview.follow_check.setChecked(False)
    window._on_render_event(saved(second))
    assert preview.current_path == first  # следование выключено — кадр не меняем

    preview.follow_check.setChecked(True)
    window._on_render_event(saved(second))
    assert preview.current_path == second


def test_preview_explains_unpreviewable_saved_frame(
    qapp, configured_store: SettingsStore, caps_loader, tmp_path: Path
) -> None:
    from brm.core.log_parser import KIND_SAVED, LogEvent

    window = MainWindow(configured_store, capabilities_loader=caps_loader)
    window.show_preview()
    exr = tmp_path / "frames" / "0007.exr"
    exr.parent.mkdir(parents=True, exist_ok=True)
    exr.write_bytes(b"not really exr")

    window._on_render_event(LogEvent(kind=KIND_SAVED, raw="", saved_path=str(exr)))
    preview = window._preview_window
    assert preview is not None and not preview.image.has_image()
    assert "OpenEXR" in preview.caption.text()


def test_preview_finds_last_frame_on_disk_without_a_render(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path
) -> None:
    """Открыли приложение после ночного рендера: кадров в памяти нет, а на диске есть."""
    from brm.core.render_plan import resolve_output_path

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)

    job = window.project_panel.current_job()
    output_path = resolve_output_path(job, window.settings, "Scene")
    frames_dir = Path(output_path).parent
    _write_png(frames_dir / "0001.png")
    last = _write_png(frames_dir / "0012.png")

    window.show_preview()
    preview = window._preview_window
    assert preview is not None
    assert preview.current_path == last and preview.image.has_image()
    assert "last frame on disk" in preview.caption.text()


def test_preview_without_frames_says_so(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path
) -> None:
    from brm.ui.preview_window import EMPTY_TEXT

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = make_window(store, caps_loader=caps_loader, project_loader=project_loader)
    load_project(qapp, window, blend_file)

    window.show_preview()
    preview = window._preview_window
    assert preview is not None and not preview.image.has_image()
    assert preview.caption.text() == EMPTY_TEXT



# --- подстройка пресета под железо ---------------------------------------------


@pytest.fixture
def cycles_project_loader(fixtures_dir: Path):
    """Тот же проект, но сцена на Cycles: подстройка трогает только секцию Cycles."""
    data = json.loads((fixtures_dir / PROJECT_FIXTURE).read_text(encoding="utf-8"))
    for scene in data["scenes"]:
        scene["engine"] = "CYCLES"

    def loader(blender_path: str, blend_path: str, *, cancel) -> ProjectInfo:
        info = ProjectInfo.model_validate(data)
        info.file_path = blend_path
        return info

    return loader


def _hardware(**kwargs):
    from brm.core.hardware import HardwareInfo

    def detector(*args, **_kwargs) -> HardwareInfo:
        return HardwareInfo(**kwargs)

    return detector


def _ready_window(qapp, store, caps_loader, project_loader, blend_file: Path, detector=None) -> MainWindow:
    window = MainWindow(
        store,
        capabilities_loader=caps_loader,
        project_loader=project_loader,
        hardware_detector=detector,
    )
    load_project(qapp, window, blend_file)
    wait_until(qapp, lambda: window._hardware_task is None)
    window.refresh_resolved_preset()
    return window


def test_an_8gb_card_gets_no_tuning_and_keeps_the_scene_tiles(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Регрессия скорости: приложение не должно трогать тайлы на целевом ноутбуке.

    Раньше подстройка понижала tile до 1024, кадр 1920x1920 бился на четыре куска
    и считался дольше. Теперь пресет тайлы не задаёт, а подстройка их не трогает.
    """
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file,
        _hardware(gpu_name="NVIDIA GeForce RTX 5070 Laptop GPU", vram_mb=8151, ram_mb=32189, cpu_threads=24),
    )
    assert window.tuning is not None and not window.tuning.changed()

    job = window.compose_job()
    assert job is not None
    assert "cycles.tile_size" not in job.overrides
    assert "cycles.use_auto_tile" not in job.overrides

    label = window.settings_form.tuning_label
    assert not label.isHidden() and "preset already fits" in label.text()


def test_preset_no_longer_forces_resolution_or_format(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Super не переписывает 50% и формат, выставленные в .blend."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    window.settings_form.preset_combo.setCurrentIndex(1)  # Super
    job = window.compose_job()
    assert job is not None
    assert "render.resolution_percentage" not in job.overrides
    assert "render.image_settings.file_format" not in job.overrides
    assert job.file_format is None  # -F не передаётся, формат берёт Blender из сцены


def test_small_card_still_gets_protection(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """На слабой машине подстройка по-прежнему нужна — но без тайлов."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=4096, ram_mb=8192)
    )
    job = window.compose_job()
    assert job is not None
    assert job.overrides["cycles.denoising_use_gpu"] is False
    assert job.overrides["render.use_persistent_data"] is False
    assert "cycles.tile_size" not in job.overrides


def test_tuning_off_keeps_the_preset_as_written(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=r"D:\out"))
    window = _ready_window(
        qapp, store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=4096, ram_mb=8192)
    )
    assert window.resolved_preset.value("cycles.denoising_use_gpu") is False

    window.settings_form.tune_check.setChecked(False)
    assert window.resolved_preset.value("cycles.denoising_use_gpu") is True
    assert window.tuning is None
    assert window.settings_form.tuning_label.isHidden()
    assert store.load().tune_for_hardware is False  # выбор переживёт перезапуск


def test_unknown_hardware_changes_nothing_in_the_ui(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = _ready_window(qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware())
    assert window.tuning is None
    assert "Hardware unknown" in window.settings_form.tuning_label.text()


def test_hardware_probe_failure_is_not_fatal(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Нет nvidia-smi, нет прав, что угодно — рендер должен остаться рабочим."""

    def broken(*args, **kwargs):
        raise OSError("nvidia-smi exploded")

    window = _ready_window(qapp, configured_store, caps_loader, cycles_project_loader, blend_file, broken)
    assert window.hardware.vram_mb is None
    assert window.render_button.isEnabled()
    assert any("nvidia-smi exploded" in line for line in window.log_view.lines())


def test_custom_value_beats_hardware_tuning(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Явно выставленное пользователем значение важнее автоматики."""
    from brm.ui.settings_form import VIEW_EXPERT

    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=4096, ram_mb=8192)
    )
    window.settings_form.set_display_mode(VIEW_EXPERT)
    row = window.settings_form.expert_form.rows["cycles.denoising_use_gpu"]
    row.set_mode(MODE_CUSTOM)
    row.set_value(True)

    job = window.compose_job()
    assert job is not None and job.overrides["cycles.denoising_use_gpu"] is True


def test_tiles_can_be_switched_off_from_the_simple_form(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Отзыв с реальной задачи: «не нашёл, где отключить рендер по тайлам»."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    row = window.settings_form.rows["tiles"]
    row.set_mode(MODE_CUSTOM)
    row.set_value(False)

    job = window.compose_job()
    assert job is not None and job.overrides["cycles.use_auto_tile"] is False


# --- три пресета и свои сохранённые ------------------------------------------------


def test_only_three_choices_are_offered(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Отзыв: «по факту необходимы лишь три пресета» — Draft, Super и свой."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    combo = window.settings_form.preset_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["Draft", "Super", "Manual"]


def test_super_warns_that_a_frame_is_slow(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    form = window.settings_form
    form.preset_combo.setCurrentIndex(0)  # Draft
    assert form.warning_label.isHidden()

    form.preset_combo.setCurrentIndex(1)  # Super
    assert not form.warning_label.isHidden() and "20 s" in form.warning_label.text()


def test_draft_halves_the_resolution_of_the_scene(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """В фикстуре сцена на 100%, значит Draft просит 50%."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    window.settings_form.preset_combo.setCurrentIndex(0)  # Draft
    job = window.compose_job()
    assert job is not None and job.overrides["render.resolution_percentage"] == 50


def test_save_and_delete_your_own_preset_from_the_form(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from brm.ui import main_window as main_window_mod

    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    monkeypatch.setattr(main_window_mod.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Cave look", True)))
    window.save_current_as_preset()

    combo = window.settings_form.preset_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["Draft", "Super", "Manual", "Cave look"]
    assert combo.currentData() == "Cave look"
    assert window.settings_form.delete_preset_button.isEnabled()  # своё удалять можно

    monkeypatch.setattr(
        main_window_mod.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    window.delete_selected_preset()
    assert [combo.itemData(i) for i in range(combo.count())] == ["Draft", "Super", "Manual"]


def test_builtin_presets_cannot_be_deleted(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    assert not window.settings_form.delete_preset_button.isEnabled()
    window.delete_selected_preset()  # ничего не делает и не падает
    combo = window.settings_form.preset_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["Draft", "Super", "Manual"]


def test_saving_over_a_builtin_name_is_refused(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path, monkeypatch
) -> None:
    from brm.ui import main_window as main_window_mod

    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    monkeypatch.setattr(main_window_mod.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Super", True)))
    window.save_current_as_preset()
    assert "built-in" in window.log_view.status_label.text()
    combo = window.settings_form.preset_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["Draft", "Super", "Manual"]


# --- упрощение главного экрана ----------------------------------------------------


def _on_main_screen(window: MainWindow, widget) -> bool:
    """Виден ли виджет на главном экране, а не в отдельном окне."""
    central = window.centralWidget()
    parent = widget.parent()
    while parent is not None:
        if parent is central:
            return True
        parent = parent.parent()
    return False


def test_main_screen_keeps_only_what_is_used(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Отзыв: очередь, видео и экспертная форма пугали нагромождением."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    assert _on_main_screen(window, window.project_panel)
    assert _on_main_screen(window, window.settings_form)
    assert _on_main_screen(window, window.log_view)
    for widget in (window.queue_view, window.video_panel, window.settings_form.expert_form):
        assert not _on_main_screen(window, widget)
    # Строки Camera и Safety с панели проекта тоже ушли.
    assert window.project_panel.camera_label.isHidden()
    assert not _on_main_screen(window, window.project_panel.safety_widget)


def test_camera_is_shown_next_to_the_frames(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Отдельной строки у камеры больше нет, но сама информация не потерялась."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    assert "camera Camera" in window.project_panel.frames_label.text()


def test_hidden_panels_open_in_their_own_windows(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    window.show_queue()
    assert window._queue_dialog is not None and not window._queue_dialog.isHidden()
    assert window._queue_dialog.view is window.queue_view

    window.show_video()
    assert window._video_dialog is not None and window._video_dialog.panel is window.video_panel

    window.show_safety()
    assert window._safety_dialog is not None
    assert window._safety_dialog.content is window.project_panel.safety_widget
    # Виджеты те же самые, значит состояние читается там же, где и раньше.
    window.project_panel.resume_check.setChecked(False)
    job = window.compose_job()
    assert job is not None and job.resume is False


def test_expert_window_takes_the_values_over_and_gives_them_back(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    from brm.ui.settings_form import VIEW_SIMPLE

    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    form = window.settings_form
    form.rows["samples"].set_mode(MODE_CUSTOM)
    form.rows["samples"].set_value(42)
    assert form.custom_values() == {"cycles.samples": 42}

    window.show_expert_settings()
    assert window._expert_window is not None and window._expert_window.form is form.expert_form
    assert form.display_mode() == "expert" and not form.view_combo.isEnabled()
    assert form.custom_values() == {}  # экспертное окно ещё ничего не трогало

    window._expert_window.accept()  # закрыли — значения снова из простых строк
    assert form.display_mode() == VIEW_SIMPLE and form.view_combo.isEnabled()
    assert form.custom_values() == {"cycles.samples": 42}


def test_build_number_is_next_to_the_author(qapp, settings_path: Path) -> None:
    from brm import __build__

    window = MainWindow(SettingsStore(settings_path))
    assert window.credit_label.text() == f"Made by Pavel Postnikov · Build {__build__}"


# --- график истории кадров ---------------------------------------------------------


def _history_window(qapp, store, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch, runs: int = 2):
    """Окно с историей из нескольких прошедших рендеров."""
    from brm.core.history import HistoryStore

    history_store = HistoryStore(tmp_path / "history.db")
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3], delay=0.02)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )
    window.history_store = history_store
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)
    for _ in range(runs):
        window.start_render()
        wait_until(qapp, lambda: window.runner.status is not None, timeout=20)
    window.show_history()
    return window


def test_chart_shows_the_selected_run_over_the_earlier_ones(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from brm.core.frame_chart import ROLE_CURRENT, ROLE_RECENT

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = _history_window(qapp, store, caps_loader, project_loader, blend_file, tmp_path, monkeypatch, runs=3)
    view = window._history_dialog.view
    view.table.selectRow(0)

    roles = [s.role for s in view.chart.series()]
    assert roles.count(ROLE_CURRENT) == 1  # выбранный прогон — синяя линия
    assert roles.count(ROLE_RECENT) == 2  # остальные два, синим не дублируются
    assert current_points(view.chart) == [(f, 0.1) for f in (1, 2, 3)]
    # В покое ось X охватывает все линии, а не только выбранную.
    assert view.chart.bounds().empty is False


def test_reference_run_is_marked_and_remembered(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from brm.core.frame_chart import ROLE_REFERENCE

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = _history_window(qapp, store, caps_loader, project_loader, blend_file, tmp_path, monkeypatch, runs=2)
    view = window._history_dialog.view
    view.table.selectRow(1)  # объявляем эталоном не тот, что выбран потом
    reference = view.selected_entry()
    assert reference is not None

    window.toggle_reference_render()
    assert store.load().reference_render_id == reference.id  # выбор переживёт перезапуск
    assert view.reference_button.text() == "Clear reference"

    view.table.selectRow(0)
    roles = [s.role for s in view.chart.series()]
    assert ROLE_REFERENCE in roles
    assert "reference" in view.chart_label.text()

    view.table.selectRow(1)
    window.toggle_reference_render()
    assert store.load().reference_render_id is None
    assert view.reference_button.text() == "Set as reference"


def test_deleting_a_run_also_clears_it_as_reference(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui import main_window as main_window_mod

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = _history_window(qapp, store, caps_loader, project_loader, blend_file, tmp_path, monkeypatch, runs=2)
    view = window._history_dialog.view
    view.table.selectRow(0)
    window.toggle_reference_render()
    assert store.load().reference_render_id is not None

    monkeypatch.setattr(
        main_window_mod.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    view.table.selectRow(0)
    window.delete_selected_render()
    assert view.table.rowCount() == 1
    assert store.load().reference_render_id is None  # эталон не должен висеть на удалённом


def test_clearing_the_whole_history(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    from brm.ui import main_window as main_window_mod

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    window = _history_window(qapp, store, caps_loader, project_loader, blend_file, tmp_path, monkeypatch, runs=2)
    monkeypatch.setattr(
        main_window_mod.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    window.clear_render_history()

    view = window._history_dialog.view
    assert view.table.rowCount() == 0 and view.chart.series() == []
    assert "History cleared, 2 render(s) removed" in window.log_view.status_label.text()
    # На живом графике красные линии исчезли, синяя от прошедшего прогона осталась:
    # это результат текущей сессии, а не запись истории.
    assert [s.role for s in window.progress_panel.chart.series()] == ["current"]


def test_live_chart_draws_earlier_runs_under_the_current_one(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    """Синяя линия идущего рендера поверх красных прошлых — идея из отзыва."""
    from brm.core.frame_chart import ROLE_CURRENT, ROLE_RECENT
    from brm.core.history import HistoryStore

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    history_store = HistoryStore(tmp_path / "history.db")
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3], delay=0.02)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )
    window.history_store = history_store
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)

    window.start_render()  # первый прогон ложится в историю
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)
    window.start_render()  # второй рисуется поверх него
    wait_until(qapp, lambda: window.runner.status is not None, timeout=20)

    roles = [s.role for s in window.progress_panel.chart.series()]
    assert roles.count(ROLE_CURRENT) == 1 and ROLE_RECENT in roles
    assert current_points(window.progress_panel.chart) == [(f, 0.1) for f in (1, 2, 3)]


# --- устройство, отсечение по камере и анализ сцены --------------------------------


def test_device_choice_reaches_the_job_and_is_remembered(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    from brm.core.capabilities import COMPUTE_CPU, COMPUTE_GPU_CPU

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=r"D:\out"))
    window = _ready_window(
        qapp, store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    form = window.settings_form
    assert form.compute_mode() == "auto"  # дефолт: лучшее из доступного

    form.device_combo.setCurrentIndex(form.device_combo.findData(COMPUTE_GPU_CPU))
    assert store.load().compute_mode == COMPUTE_GPU_CPU  # выбор переживёт перезапуск
    job = window.compose_job()
    assert job is not None and job.compute_mode == COMPUTE_GPU_CPU

    form.device_combo.setCurrentIndex(form.device_combo.findData(COMPUTE_CPU))
    assert window.compose_job().compute_mode == COMPUTE_CPU


def test_camera_culling_checkbox_reaches_the_job(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    assert window.compose_job().camera_cull is False
    window.settings_form.cull_check.setChecked(True)
    assert window.compose_job().camera_cull is True


def test_analyze_button_needs_a_project(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=cycles_project_loader)
    assert not window.project_panel.analyze_button.isEnabled()
    load_project(qapp, window, blend_file)
    assert window.project_panel.analyze_button.isEnabled()


def test_scene_analysis_shows_numbers_and_survives_failure(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path, monkeypatch
) -> None:
    from brm.core.scene_stats import SceneStats
    from brm.ui import main_window as main_window_mod

    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    stats = SceneStats(
        objects=9, meshes=3, triangles=63048, instances=4, instanced_triangles=48,
        objects_by_type={"MESH": 3, "EMPTY": 4}, camera_culled_objects=1,
    )
    monkeypatch.setattr(main_window_mod, "analyze_scene", lambda *a, **k: stats)
    window.analyze_scene()
    wait_until(qapp, lambda: window._analysis_task is None, timeout=15)

    dialog = window._analysis_dialog
    assert dialog is not None and not dialog.isHidden()
    assert dialog.rows["objects"].text() == "9" and dialog.rows["instances"].text() == "4"
    assert "63.0 k" in dialog.rows["triangles"].text()
    assert "mesh 3" in dialog.rows["objects_by_type"].text()
    assert any("scene analysis" in line for line in window.log_view.lines())

    def broken(*args, **kwargs):
        raise RuntimeError("blender exploded")

    monkeypatch.setattr(main_window_mod, "analyze_scene", broken)
    window.analyze_scene()
    wait_until(qapp, lambda: window._analysis_task is None, timeout=15)
    assert "blender exploded" in dialog.status_label.text()
    assert window.render_button.isEnabled()  # неудачный анализ не ломает рендер


# --- отзыв по билду 3001 -----------------------------------------------------------


def test_scene_and_layer_rows_hide_when_there_is_nothing_to_choose(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Отзыв: «не совсем ясно, зачем эта зона нужна». В файле одна сцена и один слой."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    panel = window.project_panel
    assert panel.scene_label.isHidden() and panel.scene_combo.isHidden()
    assert panel.view_layer_label.isHidden() and panel.view_layer_combo.isHidden()
    # Задача всё равно знает сцену и слой: скрыт только выбор, а не значение.
    job = window.compose_job()
    assert job is not None and job.scene == "Scene"


def test_scene_row_comes_back_when_the_file_has_several(
    qapp, configured_store: SettingsStore, caps_loader, fixtures_dir: Path, blend_file: Path
) -> None:
    data = json.loads((fixtures_dir / PROJECT_FIXTURE).read_text(encoding="utf-8"))
    second = json.loads(json.dumps(data["scenes"][0]))
    second["name"] = "Scene Extra"
    data["scenes"].append(second)

    def loader(blender_path: str, blend_path: str, *, cancel) -> ProjectInfo:
        info = ProjectInfo.model_validate(data)
        info.file_path = blend_path
        return info

    window = MainWindow(configured_store, capabilities_loader=caps_loader, project_loader=loader)
    load_project(qapp, window, blend_file)
    panel = window.project_panel
    assert not panel.scene_label.isHidden() and not panel.scene_combo.isHidden()
    assert panel.scene_combo.count() == 2


def test_manual_is_the_workbench_for_your_own_presets(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, cycles_project_loader, blend_file: Path, monkeypatch
) -> None:
    """Отзыв: «Manual — способ создания собственных пресетов».

    Настроил в Manual, сохранил под именем, позже загрузил и получил всё обратно.
    """
    from brm.ui import main_window as main_window_mod

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=r"D:\out"))
    window = _ready_window(
        qapp, store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    form = window.settings_form
    combo = form.preset_combo

    # Пустой Manual наполняется значениями пресета, с которого на него ушли.
    combo.setCurrentIndex(combo.findData("Super"))
    super_samples = window.resolved_preset.value("cycles.samples")
    combo.setCurrentIndex(combo.findData("Manual"))
    assert store.load().manual_overrides["cycles.samples"] == super_samples
    assert window.resolved_preset.value("cycles.samples") == super_samples

    # Правка в Manual запоминается и переживает перезапуск.
    form.rows["samples"].set_mode(MODE_CUSTOM)
    form.rows["samples"].set_value(96)
    window._flush_manual()
    assert store.load().manual_overrides["cycles.samples"] == 96

    # И сохраняется под своим именем со всеми настройками.
    monkeypatch.setattr(main_window_mod.QInputDialog, "getText", staticmethod(lambda *a, **k: ("RM-Cycles", True)))
    window.save_current_as_preset()
    saved = next(p for p in window.presets if p.name == "RM-Cycles")
    assert saved.cycles["cycles.samples"] == 96
    assert combo.currentData() == "RM-Cycles"


def test_manual_cannot_be_deleted(
    qapp, configured_store: SettingsStore, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Manual — всегда третья строка списка, а не сохранённый файл."""
    window = _ready_window(
        qapp, configured_store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    combo = window.settings_form.preset_combo
    combo.setCurrentIndex(combo.findData("Manual"))
    assert not window.settings_form.delete_preset_button.isEnabled()


def test_editing_outside_manual_does_not_touch_it(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, cycles_project_loader, blend_file: Path
) -> None:
    """Правка поверх Draft остаётся правкой поверх Draft и никуда не перескакивает."""
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=r"D:\out"))
    window = _ready_window(
        qapp, store, caps_loader, cycles_project_loader, blend_file, _hardware(vram_mb=8151, ram_mb=32189)
    )
    form = window.settings_form
    form.preset_combo.setCurrentIndex(form.preset_combo.findData("Draft"))
    form.rows["samples"].set_mode(MODE_CUSTOM)
    form.rows["samples"].set_value(17)
    window._flush_manual()

    assert form.current_preset_name() == "Draft"
    assert store.load().manual_overrides == {}
    assert window.compose_job().overrides["cycles.samples"] == 17


def test_a_stopped_render_lands_in_the_history(
    qapp, settings_path: Path, fake_blender: Path, caps_loader, project_loader, blend_file: Path, tmp_path: Path, monkeypatch
) -> None:
    """Остановленный рендер — обычный рабочий сценарий, а не сбой.

    Павел штатно жмёт Stop, чтобы пересобрать или переставить билд. Если такой
    прогон не попадает в историю, его кадры не лягут на график, ради которого
    история и ведётся.
    """
    from brm.core.history import HistoryStore

    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender), default_output_dir=str(tmp_path / "out")))
    history_store = HistoryStore(tmp_path / "history.db")
    builder = FakePlanBuilder(tmp_path, base_frames=list(range(1, 31)), delay=0.3)
    window = make_window(
        store, caps_loader=caps_loader, project_loader=project_loader, fake_builder=builder, monkeypatch=monkeypatch
    )
    window.history_store = history_store
    load_project(qapp, window, blend_file)
    window.project_panel.resume_check.setChecked(False)

    window.start_render()
    wait_until(qapp, lambda: window.runner.tracker is not None and window.runner.tracker.progress.frames_done_count >= 1)
    window.stop_render()
    wait_until(qapp, lambda: window.runner.status is not None, timeout=30)
    assert window.runner.status == "stopped"

    entries = history_store.list_entries()
    assert len(entries) == 1, [line for line in window.log_view.lines() if "history" in line]
    assert entries[0].status == "stopped"
    assert 1 <= entries[0].frames_done < 30  # успел не всё, и это записано честно
    assert entries[0].stats_path
