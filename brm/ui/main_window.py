"""Главное окно. Только отображение: статус Blender, проект, задача и очередь — из core.

Пробы выполняются в фоне (``ui.workers``), рендер ведёт ``core.job_runner``.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from brm import __author__, __version__
from brm.core.blender_locator import validate_blender_path
from brm.core.capabilities import Capabilities, get_capabilities, support_problem
from brm.core.job_runner import RUN_FAILED, RUN_PAUSED, RUN_STOPPED, RUN_SUCCESS, JobRunner
from brm.core.log_parser import KIND_OTHER
from brm.core.models import RenderJob
from brm.core.preset_resolver import ResolvedPreset, compose_overrides, display_file_format, resolve_preset
from brm.core.presets import Preset, find_preset, load_presets
from brm.core.project_probe import ProjectInfo, probe_project, project_warnings
from brm.core.queue import QueueStore, RenderQueue
from brm.core.render_plan import RenderPlan
from brm.core.render_stats import diagnose_failure, format_duration
from brm.core.storage import SettingsStore, cache_dir, tmp_dir, with_recent_project
from brm.ui.banner import WarningBanner
from brm.ui.log_view import LogView
from brm.ui.progress_panel import ProgressPanel
from brm.ui.project_panel import ProjectPanel, blend_paths_from_mime
from brm.ui.queue_view import QueueView
from brm.ui.settings_dialog import SettingsDialog
from brm.ui.settings_form import SettingsForm
from brm.ui.theme import apply_theme
from brm.ui.workers import FunctionTask

CREDIT_TEXT = f"Made by {__author__}"
CAPABILITIES_TIMEOUT = 180.0
# Тяжёлый .blend может грузиться минуты, поэтому таймаут щедрый, а есть кнопка Cancel.
PROJECT_TIMEOUT = 600.0

CapabilitiesLoader = Callable[..., Capabilities]
ProjectLoader = Callable[..., ProjectInfo]


def default_capabilities_loader(blender_path: str, *, cancel: threading.Event) -> Capabilities:
    return get_capabilities(
        blender_path, cache_dir=cache_dir(), tmp_dir=tmp_dir(), timeout=CAPABILITIES_TIMEOUT, cancel=cancel
    )


def default_project_loader(blender_path: str, blend_path: str, *, cancel: threading.Event) -> ProjectInfo:
    return probe_project(blender_path, blend_path, tmp_dir=tmp_dir(), timeout=PROJECT_TIMEOUT, cancel=cancel)


class MainWindow(QMainWindow):
    def __init__(
        self,
        store: SettingsStore,
        parent: QWidget | None = None,
        *,
        capabilities_loader: CapabilitiesLoader | None = None,
        project_loader: ProjectLoader | None = None,
        queue_store: QueueStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self.settings = store.load()
        self._caps_loader = capabilities_loader or default_capabilities_loader
        self._project_loader = project_loader or default_project_loader

        self.capabilities: Capabilities | None = None
        self.capabilities_error: str | None = None
        self._capabilities_error_path: str | None = None
        self.project: ProjectInfo | None = None
        self._caps_task: FunctionTask | None = None
        self._project_task: FunctionTask | None = None

        self.runner = JobRunner(self)
        self.runner.started.connect(self._on_render_started)
        self.runner.chunk_started.connect(self._on_chunk_started)
        self.runner.line_received.connect(self._on_render_line)
        self.runner.event_received.connect(self._on_render_event)
        self.runner.finished.connect(self._on_render_finished)
        self._render_started_at = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._refresh_progress)

        self.queue_store = queue_store or QueueStore()
        self.queue: RenderQueue = self.queue_store.load()
        self._queue_running = False
        self._active_queue_item: str | None = None

        self.presets: list[Preset] = load_presets()
        self.resolved_preset: ResolvedPreset | None = None

        self.setWindowTitle(f"BRM — Blender Render Manager {__version__}")
        self.resize(1200, 760)
        self.setAcceptDrops(True)

        self._build_menu()
        self._build_central()
        self.project_panel.set_recent(self.settings.recent_projects)
        self.project_panel.set_default_output_dir(self.settings.default_output_dir or "")
        self.settings_form.set_presets(self.presets, self.settings.last_preset)
        self.settings_form.preset_changed.connect(self._on_preset_changed)
        self.project_panel.scene_combo.currentTextChanged.connect(lambda _name: self.refresh_resolved_preset())
        self.queue_view.set_items(self.queue.items)
        self.refresh_blender_status()

    # --- построение ----------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open .blend…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.project_panel_browse)
        file_menu.addAction(open_action)

        self.settings_action = QAction("&Settings…", self)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(self.settings_action)

        reprobe_action = QAction("&Re-check Blender", self)
        reprobe_action.triggered.connect(self.reprobe_blender)
        file_menu.addAction(reprobe_action)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About BRM", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_central(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.banner = WarningBanner(central)
        self.banner.action_clicked.connect(self.open_settings)
        root.addWidget(self.banner)

        top = QWidget(central)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 8, 12, 8)
        self.blender_label = QLabel(top)
        top_layout.addWidget(self.blender_label, 1)
        self.render_button = QPushButton("Render", top)
        self.render_button.setObjectName("primaryButton")  # единственная акцентная кнопка
        self.render_button.setMinimumWidth(140)
        self.render_button.clicked.connect(self.start_render)
        top_layout.addWidget(self.render_button, 0)
        self.pause_button = QPushButton("Pause", top)
        self.pause_button.setMinimumWidth(100)
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Pause after the current frame; Resume renders the remaining frames")
        self.pause_button.clicked.connect(self.pause_or_resume)
        top_layout.addWidget(self.pause_button, 0)
        self.stop_button = QPushButton("Stop", top)
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setMinimumWidth(100)
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Stop after terminate; kill if Blender ignores it for 5 s. Stops the queue too")
        self.stop_button.clicked.connect(self.stop_render)
        top_layout.addWidget(self.stop_button, 0)
        root.addWidget(top)

        self.project_panel = ProjectPanel()
        self.project_panel.file_requested.connect(self.open_project)
        self.settings_form = SettingsForm()
        self.progress_panel = ProgressPanel()
        self.log_view = LogView()
        self.queue_view = QueueView()
        self.queue_view.add_requested.connect(self.add_current_to_queue)
        self.queue_view.run_requested.connect(self.run_queue)
        self.queue_view.remove_requested.connect(self.remove_queue_items)
        self.queue_view.clear_requested.connect(self.clear_finished_queue)

        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.project_panel)
        left.addWidget(self.settings_form)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.progress_panel)
        right.addWidget(self.log_view)
        right.addWidget(self.queue_view)
        right.setSizes([230, 330, 180])
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(left)
        main_split.addWidget(right)
        main_split.setSizes([560, 640])
        root.addWidget(main_split, 1)

        self.setCentralWidget(central)

        # Статус-бар: слева прогресс фоновой задачи с Cancel, справа подпись автора.
        self.task_label = QLabel(self)
        self.task_label.hide()
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.cancel_tasks)
        self.cancel_button.hide()
        self.statusBar().addWidget(self.task_label, 1)
        self.statusBar().addWidget(self.cancel_button)
        self.credit_label = QLabel(CREDIT_TEXT, self)
        self.statusBar().addPermanentWidget(self.credit_label)

    # --- состояние Blender ---------------------------------------------------

    def refresh_blender_status(self) -> None:
        """Три уровня индикации из раздела 4.1: кнопка, тултип, баннер.

        Путь проверяется файлово, затем через пробу возможностей (кэшируется).
        """
        status = validate_blender_path(self.settings.blender_path)
        if not status.ok:
            self.capabilities = None
            self._show_blender_problem(status.reason)
            return
        if self.capabilities is not None and self.capabilities.blender_path == status.path:
            problem = support_problem(self.capabilities)
            if problem:
                self._show_blender_problem(problem)
            else:
                self._show_blender_ok(self.capabilities)
            return
        if self.capabilities_error and self._capabilities_error_path == status.path:
            self._show_blender_problem(self.capabilities_error)
            return
        if self._caps_task is None:
            self._start_capabilities_probe(status.path)

    def reprobe_blender(self) -> None:
        self.capabilities = None
        self.capabilities_error = None
        self.refresh_blender_status()

    def _start_capabilities_probe(self, blender_path: str) -> None:
        task = FunctionTask(self._caps_loader, blender_path)
        task.tag = blender_path
        task.signals.finished.connect(lambda caps, t=task: self._on_capabilities(t, caps))
        task.signals.failed.connect(lambda message, t=task: self._on_capabilities_failed(t, message))
        self._caps_task = task
        self._show_blender_probing()
        self._begin_task("Checking Blender…")
        task.start()

    def _on_capabilities(self, task: FunctionTask, caps: Capabilities) -> None:
        if task is not self._caps_task:
            return
        self._caps_task = None
        self._end_task()
        self.capabilities = caps
        self.capabilities_error = None
        formats = caps.property("image_settings", "file_format")
        if formats is not None and formats.enum_items:
            self.settings_form.set_format_choices(formats.enum_identifiers())
        self.refresh_blender_status()
        self.refresh_resolved_preset()

    def _on_capabilities_failed(self, task: FunctionTask, message: str) -> None:
        if task is not self._caps_task:
            return
        self._caps_task = None
        self._end_task()
        self.capabilities = None
        self.capabilities_error = message
        self._capabilities_error_path = task.tag
        self.refresh_blender_status()

    def _show_blender_problem(self, reason: str) -> None:
        first_line = reason.strip().splitlines()[0] if reason.strip() else reason
        self.banner.set_message(f"Blender is not configured: {first_line}")
        self.banner.setToolTip(reason)
        self.banner.show()
        self.render_button.setEnabled(False)
        self.render_button.setToolTip(reason)
        self.blender_label.setText("Blender is not configured")

    def _show_blender_probing(self) -> None:
        self.banner.hide()
        self.render_button.setEnabled(False)
        self.render_button.setToolTip("Checking Blender capabilities…")
        self.blender_label.setText("Checking Blender…")

    def _show_blender_ok(self, caps: Capabilities) -> None:
        self.banner.hide()
        self.render_button.setEnabled(not self.runner.is_running())
        self.render_button.setToolTip("Start rendering the current project")
        engines = ", ".join(caps.engines)
        self.blender_label.setText(
            f"Blender {caps.version_string} · Cycles device: {caps.best_cycles_device()} · {engines}"
        )
        self.blender_label.setToolTip(caps.blender_path)

    # --- пресеты -----------------------------------------------------------------

    def current_preset(self) -> Preset | None:
        return find_preset(self.presets, self.settings_form.current_preset_name())

    def _on_preset_changed(self, name: str) -> None:
        if name != self.settings.last_preset:
            self.settings = self.settings.model_copy(update={"last_preset": name})
            self._store.save(self.settings)
        self.refresh_resolved_preset()

    def refresh_resolved_preset(self) -> None:
        """Пресет + capabilities + движок сцены → значения в форме. Логики нет, только вызов core."""
        preset = self.current_preset()
        scene = None
        if self.project is not None:
            scene = self.project.scene(self.project_panel.scene_combo.currentText()) or self.project.default_scene()
        if preset is None or self.capabilities is None or scene is None:
            self.resolved_preset = None
            self.settings_form.set_engine(None)
            self.settings_form.show_resolved(None)
            return
        self.resolved_preset = resolve_preset(preset, self.capabilities, scene.engine)
        self.settings_form.set_engine(self.resolved_preset.engine)
        self.settings_form.show_resolved(self.resolved_preset)

    # --- проект ----------------------------------------------------------------

    def project_panel_browse(self) -> None:
        self.project_panel.browse()

    def open_project(self, path: str) -> None:
        path = os.path.normpath(path)
        if not Path(path).is_file():
            self.project_panel.set_error(f"File not found: {path}")
            return
        if self.capabilities is None:
            self.project_panel.set_error("Configure a working Blender first (File → Settings)")
            return
        if self._project_task is not None:
            self._project_task.cancel.set()
        task = FunctionTask(self._project_loader, self.capabilities.blender_path, path)
        task.tag = path
        task.signals.finished.connect(lambda info, t=task: self._on_project_loaded(t, info))
        task.signals.failed.connect(lambda message, t=task: self._on_project_failed(t, message))
        self._project_task = task
        self.project_panel.set_loading(path)
        self._begin_task(f"Reading {Path(path).name}…")
        task.start()

    def _on_project_loaded(self, task: FunctionTask, info: ProjectInfo) -> None:
        if task is not self._project_task:
            return
        self._project_task = None
        self._end_task()
        self.project = info
        self.project_panel.set_project(info, project_warnings(info))
        self.settings = with_recent_project(self.settings, task.tag)
        self._store.save(self.settings)
        self.project_panel.set_recent(self.settings.recent_projects)
        self.refresh_resolved_preset()

    def _on_project_failed(self, task: FunctionTask, message: str) -> None:
        if task is not self._project_task:
            return
        self._project_task = None
        self._end_task()
        self.project = None
        self.project_panel.set_error(f"Could not read {Path(task.tag).name}: {message}")

    # --- задача из панелей ------------------------------------------------------

    def compose_job(self) -> RenderJob | None:
        """Задача из панели проекта с настройками пресета и формы. None, если проект не загружен."""
        job = self.project_panel.current_job()
        if job is None or self.project is None:
            return None
        self.refresh_resolved_preset()
        resolved = self.resolved_preset
        if resolved is None:
            return job
        overrides = compose_overrides(resolved, self.settings_form.custom_values(), self.settings_form.untouched_paths())
        chunk_size = job.chunk_size if job.chunk_size is not None else resolved.preset.chunk_size
        return job.model_copy(
            update={
                "overrides": overrides,
                "preset": resolved.preset.name,
                "engine": resolved.engine if resolved.preset.engine else None,
                "file_format": display_file_format(overrides, job.file_format),
                "chunk_size": chunk_size,
            }
        )

    # --- рендер ------------------------------------------------------------------

    def start_render(self) -> None:
        if self.runner.is_running():
            return
        if self.capabilities is None:
            self.log_view.set_status("Configure a working Blender first", "error")
            return
        job = self.compose_job()
        if job is None or self.project is None:
            self.log_view.set_status("Load a project first", "error")
            return
        self._active_queue_item = None
        self._launch(job, self.project)

    def _launch(self, job: RenderJob, project: ProjectInfo) -> bool:
        assert self.capabilities is not None
        try:
            self.runner.start(job, self.capabilities, self.settings, project, tmp_dir=tmp_dir())
        except (ValueError, RuntimeError) as exc:
            self.log_view.set_status(str(exc), "error")
            return False
        return True

    def _on_render_started(self) -> None:
        total = self.runner.frames_expected()
        self.log_view.clear()
        self.log_view.set_status(f"Rendering {total} frame(s)…", "muted")
        self.progress_panel.set_running(total)
        self._render_started_at = time.monotonic()
        self.render_button.setEnabled(False)
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._elapsed_timer.start()

    def _on_chunk_started(self, plan: RenderPlan) -> None:
        self.log_view.set_command(plan.command_line)
        if len(self.runner.plans) == 1:
            self.log_view.append_line(f"[BRM] output: {plan.output_path}")
            if plan.job.preset:
                self.log_view.append_line(f"[BRM] preset: {plan.job.preset}, {len(plan.job.overrides)} setting(s)")
            resolved = self.resolved_preset
            if resolved is not None and self._active_queue_item is None:
                for skipped in resolved.skipped:
                    self.log_view.append_line(f"[BRM] SKIP preset {skipped.path}: {skipped.reason}")
        self.log_view.append_line(f"[BRM] log file: {plan.log_path}")

    def stop_render(self) -> None:
        self._queue_running = False
        if self.runner.is_running():
            self.log_view.set_status("Stopping…", "warning")
            self.runner.stop()

    def pause_or_resume(self) -> None:
        """Пауза после текущего кадра; Resume дорендеривает оставшиеся кадры."""
        if self.runner.is_running():
            self.runner.pause()
            self.pause_button.setEnabled(False)
            self.log_view.set_status("Pausing after the current frame…", "warning")
            self.progress_panel.status_label.setText("Pausing after the current frame…")
        elif self.runner.is_paused():
            self.runner.resume()
            self.render_button.setEnabled(False)
            self.pause_button.setText("Pause")
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self._elapsed_timer.start()

    def _on_render_line(self, line: str) -> None:
        self.log_view.append_line(line)

    def _on_render_event(self, event) -> None:
        if event.kind != KIND_OTHER:
            self._refresh_progress()

    def _chunk_note(self) -> str:
        if len(self.runner.chunks) > 1:
            return f"chunk {min(self.runner.chunk_index + 1, len(self.runner.chunks))} / {len(self.runner.chunks)}"
        return ""

    def _refresh_progress(self) -> None:
        if self.runner.tracker is None:
            return
        elapsed = time.monotonic() - self._render_started_at
        self.progress_panel.update_progress(self.runner.tracker.progress, elapsed, note=self._chunk_note())

    def _on_render_finished(self, status: str) -> None:
        self._elapsed_timer.stop()
        elapsed = time.monotonic() - self._render_started_at
        runner = self.runner
        progress = runner.tracker.progress if runner.tracker is not None else None
        done = progress.frames_done_count if progress else 0
        total = progress.frames_total if progress else 0
        skipped = len(runner.skipped_existing)
        prefix = f"{skipped} on disk, " if skipped else ""
        hint = None
        if status == RUN_PAUSED:
            text, role = f"Paused after {done} / {total} frame(s), {len(runner.paused_frames)} left. Press Resume", "warning"
        elif status == RUN_SUCCESS:
            text, role = f"Finished: {prefix}{done} / {total} frame(s) rendered in {format_duration(elapsed)}", "ok"
        elif status == RUN_STOPPED:
            text, role = f"Stopped by user after {done} / {total} frame(s), {format_duration(elapsed)}", "warning"
        else:
            text, role = f"Failed after {done} / {total} frame(s): {runner.message}, {format_duration(elapsed)}", "error"
            hint = diagnose_failure(progress.errors if progress else [], runner.exit_code, RUN_FAILED)
        if runner.retry_notes:
            text += f" · retried with {runner.retry_notes[-1]}"
        self.log_view.set_status(text, role)
        self._refresh_progress()
        self.progress_panel.set_finished(text, role, hint)
        if runner.plans:
            self.log_view.append_line(f"[BRM] finished: status={status} · {runner.message} · stats={runner.plans[0].stats_path}")
        paused = runner.is_paused()
        self.pause_button.setEnabled(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        self.stop_button.setEnabled(False)
        self.refresh_blender_status()
        self._finish_queue_item(status, done, total)

    # --- очередь ----------------------------------------------------------------------

    def _save_queue(self) -> None:
        self.queue_store.save(self.queue)
        self.queue_view.set_items(self.queue.items)
        self.queue_view.set_running(self._queue_running)

    def add_current_to_queue(self) -> None:
        job = self.compose_job()
        if job is None or self.project is None:
            self.log_view.set_status("Load a project first", "error")
            return
        item = self.queue.add(job, self.project)
        self._save_queue()
        self.log_view.set_status(f"Queued {item.title}: {job.preset or 'file settings'}", "muted")

    def remove_queue_items(self, item_ids: list[str]) -> None:
        if self.queue.remove(item_ids):
            self._save_queue()

    def clear_finished_queue(self) -> None:
        if self.queue.clear_finished():
            self._save_queue()

    def run_queue(self) -> None:
        if self.runner.is_running() or self._queue_running:
            return
        if self.capabilities is None:
            self.log_view.set_status("Configure a working Blender first", "error")
            return
        self._queue_running = True
        self.queue_view.set_running(True)
        self._run_next_queue_item()

    def _run_next_queue_item(self) -> None:
        item = self.queue.next_pending()
        if item is None:
            self._queue_running = False
            self._active_queue_item = None
            self._save_queue()
            self.log_view.set_status("Queue finished", "ok")
            return
        item.status = "running"
        item.message = ""
        self._active_queue_item = item.id
        self._save_queue()
        if not self._launch(item.job, item.project):
            item.status = "failed"
            item.message = self.log_view.status_label.text()
            self._save_queue()
            self._run_next_queue_item()

    def _finish_queue_item(self, status: str, done: int, total: int) -> None:
        item_id = self._active_queue_item
        if item_id is None:
            return
        item = self.queue.find(item_id)
        if item is not None:
            item.status = {RUN_SUCCESS: "done", RUN_PAUSED: "paused", RUN_STOPPED: "stopped"}.get(status, "failed")
            item.message = self.runner.message
            item.frames_done, item.frames_total = done, total
            if status != RUN_PAUSED:
                item.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        if status == RUN_PAUSED:
            self._save_queue()
            return
        self._active_queue_item = None
        if self._queue_running and status in (RUN_SUCCESS, RUN_FAILED):
            self._save_queue()
            self._run_next_queue_item()
        else:
            self._queue_running = False
            self._save_queue()

    # --- фоновые задачи ------------------------------------------------------

    def _begin_task(self, text: str) -> None:
        self.task_label.setText(text)
        self.task_label.show()
        self.cancel_button.show()

    def _end_task(self) -> None:
        if self._caps_task is None and self._project_task is None:
            self.task_label.hide()
            self.cancel_button.hide()

    def cancel_tasks(self) -> None:
        for task in (self._caps_task, self._project_task):
            if task is not None:
                task.cancel.set()

    # --- прочее ------------------------------------------------------------------

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.focus_blender_path()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.result_settings()
            self._store.save(self.settings)
            apply_theme(QApplication.instance(), self.settings.theme)
            self.project_panel.set_default_output_dir(self.settings.default_output_dir or "")
            self.reprobe_blender()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About BRM",
            f"<b>BRM — Blender Render Manager</b><br>Version {__version__}<br><br>{CREDIT_TEXT}",
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 — имя из Qt
        if blend_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 — имя из Qt
        paths = blend_paths_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.open_project(paths[0])

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — имя из Qt
        self.cancel_tasks()
        self._queue_running = False
        if self.runner.is_running():
            self.runner.stop()
        super().closeEvent(event)

    def event(self, event: QEvent) -> bool:
        # Путь мог протухнуть, пока окно было в фоне (переустановили Blender).
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "render_button"):
            self.refresh_blender_status()
        return super().event(event)
