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
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from brm import __author__, __build__, __version__
from brm.core.blender_locator import validate_blender_path
from brm.core.capabilities import Capabilities, get_capabilities, support_problem
from brm.core.ffmpeg import (
    FfmpegError,
    VideoPreset,
    build_ffmpeg_argv,
    default_output_file,
    find_sequence,
    find_video_preset,
    load_video_presets,
    validate_ffmpeg_path,
)
from dataclasses import replace

from brm.core.frame_chart import build_series
from brm.core.history import HistoryEntry, HistoryStore, read_frame_times
from brm.core.job_runner import RUN_FAILED, RUN_PAUSED, RUN_STOPPED, RUN_SUCCESS, JobRunner
from brm.core.log_parser import KIND_OTHER, KIND_SAVED
from brm.core.models import RenderJob
from brm.core.hardware import HardwareInfo, detect_hardware
from brm.core.hardware_tuning import TuningResult, tune_preset
from brm.core.preset_resolver import (
    ResolvedPreset,
    compose_overrides,
    display_file_format,
    resolve_engine,
    resolve_preset,
)
from brm.core.preview import describe_unpreviewable, is_previewable, latest_rendered_frame
from brm.core.presets import (
    Preset,
    PresetError,
    delete_user_preset,
    find_preset,
    load_presets,
    preset_from_overrides,
    save_user_preset,
)
from brm.core.project_probe import ProjectInfo, probe_project, project_warnings
from brm.core.queue import QueueStore, RenderQueue
from brm.core.render_plan import RenderPlan, resolve_output_path
from brm.core.render_stats import diagnose_failure, format_duration
from brm.core.storage import AppSettings, SettingsStore, cache_dir, tmp_dir, with_recent_project
from brm.core.system_actions import SHUTDOWN_DELAY_S, cancel_shutdown, schedule_shutdown
from brm.core.video_runner import VIDEO_SUCCESS, VideoProcess, describe_result
from brm.ui.banner import WarningBanner
from brm.ui.expert_window import ExpertWindow
from brm.ui.history_dialog import HistoryDialog
from brm.ui.log_view import LogView
from brm.ui.notifications import Notifier
from brm.ui.progress_panel import ProgressPanel
from brm.ui.project_panel import ProjectPanel, blend_paths_from_mime
from brm.ui.queue_dialog import QueueDialog
from brm.ui.queue_view import QueueView
from brm.ui.onboarding_dialog import OnboardingDialog
from brm.ui.preview_window import PreviewWindow
from brm.ui.safety_dialog import SafetyDialog
from brm.ui.settings_dialog import SettingsDialog
from brm.ui.settings_form import SettingsForm
from brm.ui.theme import apply_theme
from brm.ui.video_dialog import VideoDialog
from brm.ui.video_panel import VideoPanel
from brm.ui.workers import FunctionTask

CREDIT_TEXT = f"Made by {__author__} · Build {__build__}"
CAPABILITIES_TIMEOUT = 180.0
# Тяжёлый .blend может грузиться минуты, поэтому таймаут щедрый, а есть кнопка Cancel.
PROJECT_TIMEOUT = 600.0

CapabilitiesLoader = Callable[..., Capabilities]
ProjectLoader = Callable[..., ProjectInfo]
HardwareDetector = Callable[..., HardwareInfo]


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
        history_store: HistoryStore | None = None,
        hardware_detector: HardwareDetector | None = None,
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
        self._queue_finished_pending = False
        self._pending_job_result: tuple[str, int, int] | None = None

        self.history_store = history_store or HistoryStore()
        self._history_dialog: HistoryDialog | None = None
        self._preview_window: PreviewWindow | None = None
        self._queue_dialog: QueueDialog | None = None
        self._safety_dialog: SafetyDialog | None = None
        self._expert_window: ExpertWindow | None = None
        self._video_dialog: VideoDialog | None = None

        self.presets: list[Preset] = load_presets()
        self.resolved_preset: ResolvedPreset | None = None
        # Железо: пока проба не пришла — пустой объект, подстройка ничего не трогает.
        self._hardware_detector = hardware_detector or detect_hardware
        self.hardware = HardwareInfo()
        self._hardware_task: FunctionTask | None = None
        self.tuning: TuningResult | None = None
        self.video_presets: list[VideoPreset] = load_video_presets()
        self.video_process = VideoProcess(self)
        self.video_process.line_received.connect(self._on_video_line)
        self.video_process.progress_changed.connect(self._on_video_progress)
        self.video_process.finished.connect(self._on_video_finished)
        self.notifier = Notifier(self)
        self.notifier.enabled = self.settings.notifications
        self._shutdown_pending = False

        self.setWindowTitle(f"BRM — Blender Render Manager {__version__}")
        self.resize(1200, 760)
        self.setAcceptDrops(True)

        self._build_menu()
        self._build_central()
        self.project_panel.set_recent(self.settings.recent_projects)
        self.project_panel.set_default_output_dir(self.settings.default_output_dir or "")
        self.settings_form.set_presets(self.presets, self.settings.last_preset)
        self.settings_form.preset_changed.connect(self._on_preset_changed)
        self.settings_form.set_tuning_enabled(self.settings.tune_for_hardware)
        self.settings_form.tuning_toggled.connect(self._on_tuning_toggled)
        self.settings_form.save_preset_requested.connect(self.save_current_as_preset)
        self.settings_form.delete_preset_requested.connect(self.delete_selected_preset)
        self.settings_form.expert_requested.connect(self.show_expert_settings)
        self.project_panel.scene_combo.currentTextChanged.connect(lambda _name: self.refresh_resolved_preset())
        self.queue_view.set_items(self.queue.items)
        self.video_panel.set_presets(self.video_presets, self.settings.last_video_preset)
        self.video_panel.set_auto_build(self.settings.auto_build_video)
        self.refresh_ffmpeg_status()
        self.refresh_blender_status()
        self._start_hardware_probe()
        self._refresh_live_chart_history()

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

        view_menu = self.menuBar().addMenu("&View")
        history_action = QAction("&History…", self)
        history_action.setShortcut(QKeySequence("Ctrl+H"))
        history_action.triggered.connect(self.show_history)
        view_menu.addAction(history_action)

        preview_action = QAction("Last rendered &frame…", self)
        preview_action.setShortcut(QKeySequence("Ctrl+P"))
        preview_action.triggered.connect(self.show_preview)
        view_menu.addAction(preview_action)

        queue_action = QAction("&Queue…", self)
        queue_action.setShortcut(QKeySequence("Ctrl+U"))
        queue_action.triggered.connect(self.show_queue)
        view_menu.addAction(queue_action)

        video_action = QAction("Assemble &video…", self)
        video_action.triggered.connect(self.show_video)
        view_menu.addAction(video_action)

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

        # Отдельная плашка на время обратного отсчёта выключения.
        self.shutdown_banner = WarningBanner(central)
        self.shutdown_banner.set_button_text("Cancel shutdown")
        self.shutdown_banner.action_clicked.connect(self.cancel_shutdown)
        self.shutdown_banner.hide()
        root.addWidget(self.shutdown_banner)

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
        self.project_panel.safety_requested.connect(self.show_safety)
        self.settings_form = SettingsForm()
        self.progress_panel = ProgressPanel()
        self.log_view = LogView()
        self.queue_view = QueueView()
        self.queue_view.add_requested.connect(self.add_current_to_queue)
        self.queue_view.run_requested.connect(self.run_queue)
        self.queue_view.remove_requested.connect(self.remove_queue_items)
        self.queue_view.clear_requested.connect(self.clear_finished_queue)
        self.video_panel = VideoPanel()
        self.video_panel.build_requested.connect(self.build_video)
        self.video_panel.stop_requested.connect(self.stop_video)

        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.project_panel)
        left.addWidget(self.settings_form)
        # Настройкам достаётся всё, что остаётся: раньше их зажимали панель видео
        # и очередь, и строки сминались до нечитаемого.
        left.setStretchFactor(1, 1)
        left.setSizes([260, 500])
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.progress_panel)
        right.addWidget(self.log_view)
        right.setSizes([260, 480])
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
        self.settings_form.set_capabilities(caps)
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

    # --- окна, вынесенные с главного экрана -----------------------------------------

    def show_queue(self) -> None:
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self.queue_view, self)
        self._queue_dialog.show()
        self._queue_dialog.raise_()
        self._queue_dialog.activateWindow()

    def show_expert_settings(self) -> None:
        """Экспертная форма в своём окне; пока оно открыто, значения берутся из него."""
        from brm.ui.settings_form import VIEW_EXPERT

        if self._expert_window is None:
            self._expert_window = ExpertWindow(self.settings_form.expert_form, self)
            self._expert_window.finished.connect(self._on_expert_closed)
        self.settings_form.set_display_mode(VIEW_EXPERT)
        self._expert_window.show()
        self._expert_window.raise_()
        self._expert_window.activateWindow()

    def _on_expert_closed(self, _result: int) -> None:
        """Окно закрыли — значения снова берутся из простых строк."""
        from brm.ui.settings_form import VIEW_SIMPLE

        self.settings_form.set_display_mode(VIEW_SIMPLE)

    def show_video(self) -> None:
        if self._video_dialog is None:
            self._video_dialog = VideoDialog(self.video_panel, self)
        self._video_dialog.show()
        self._video_dialog.raise_()
        self._video_dialog.activateWindow()

    def show_safety(self) -> None:
        if self._safety_dialog is None:
            self._safety_dialog = SafetyDialog(self.project_panel.safety_widget, self)
        self._safety_dialog.show()
        self._safety_dialog.raise_()
        self._safety_dialog.activateWindow()

    # --- свои пресеты --------------------------------------------------------------

    def save_current_as_preset(self) -> None:
        """Текущие значения формы — в именованный пресет пользователя."""
        job = self.compose_job()
        if job is None or not job.overrides:
            self.log_view.set_status("Load a project first: there is nothing to save yet", "warning")
            return
        current = self.settings_form.current_preset_name() or ""
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=f"{current} copy".strip())
        name = name.strip()
        if not ok or not name:
            return
        if any(p.builtin and p.name == name for p in self.presets):
            self.log_view.set_status(f"{name} is a built-in preset, pick another name", "error")
            return
        preset = preset_from_overrides(name, job.overrides, description="Your own preset.")
        try:
            save_user_preset(preset)
        except PresetError as exc:
            self.log_view.set_status(str(exc), "error")
            return
        self.reload_presets(select=name)
        self.log_view.set_status(f"Preset {name} saved", "ok")

    def delete_selected_preset(self) -> None:
        preset = self.settings_form.current_preset()
        if preset is None or preset.builtin:
            return
        answer = QMessageBox.question(self, "Delete preset", f"Delete preset {preset.name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_user_preset(preset.name)
        except PresetError as exc:
            self.log_view.set_status(str(exc), "error")
            return
        self.reload_presets()
        self.log_view.set_status(f"Preset {preset.name} deleted", "ok")

    def reload_presets(self, select: str | None = None) -> None:
        self.presets = load_presets()
        target = select or self.settings_form.current_preset_name() or self.settings.last_preset
        self.settings_form.set_presets(self.presets, target)
        self.refresh_resolved_preset()

    # --- железо ------------------------------------------------------------------

    def _start_hardware_probe(self) -> None:
        """nvidia-smi занимает заметное время — в поток, чтобы окно не мёрзло."""
        task = FunctionTask(self._hardware_detector)
        task.signals.finished.connect(lambda info, t=task: self._on_hardware(t, info))
        task.signals.failed.connect(lambda message, t=task: self._on_hardware_failed(t, message))
        self._hardware_task = task
        task.start()

    def _on_hardware(self, task: FunctionTask, info: HardwareInfo) -> None:
        if task is not self._hardware_task:
            return
        self._hardware_task = None
        self.hardware = info
        for note in info.notes:
            self.log_view.append_line(f"[BRM] hardware: {note}")
        self.refresh_resolved_preset()

    def _on_hardware_failed(self, task: FunctionTask, message: str) -> None:
        """Проба железа не критична: без неё подстройка просто выключена."""
        if task is not self._hardware_task:
            return
        self._hardware_task = None
        self.log_view.append_line(f"[BRM] hardware probe failed: {message}")
        self.refresh_resolved_preset()

    def _on_tuning_toggled(self, enabled: bool) -> None:
        self.settings.tune_for_hardware = enabled
        self._store.save(self.settings)
        self.refresh_resolved_preset()

    def tuned_preset(self, preset: Preset, scene_engine: str) -> Preset:
        """Пресет, урезанный под эту машину. Решение целиком в core, здесь только вызов."""
        self.tuning = None
        if not self.settings_form.tuning_enabled() or self.capabilities is None:
            self.settings_form.show_tuning(self.hardware.summary(), None)
            return preset
        if not self.hardware.is_known():
            self.settings_form.show_tuning(self.hardware.summary(), None)
            return preset
        engine = resolve_engine(preset, self.capabilities, scene_engine)
        self.tuning = tune_preset(preset, self.hardware, engine)
        self.settings_form.show_tuning(self.hardware.summary(), self.tuning.notes)
        return self.tuning.preset

    # --- пресет --------------------------------------------------------------------

    def refresh_resolved_preset(self) -> None:
        """Пресет + железо + capabilities + движок сцены → значения в форме. Логики нет, только вызов core."""
        preset = self.current_preset()
        scene = None
        if self.project is not None:
            scene = self.project.scene(self.project_panel.scene_combo.currentText()) or self.project.default_scene()
        if preset is None or self.capabilities is None or scene is None:
            self.resolved_preset = None
            self.tuning = None
            self.settings_form.set_engine(None)
            self.settings_form.show_resolved(None)
            return
        self.resolved_preset = resolve_preset(
            self.tuned_preset(preset, scene.engine),
            self.capabilities,
            scene.engine,
            scene_percentage=scene.resolution_percentage,
        )
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
            # Что подстроено под железо — в лог рядом с командой: через месяц
            # по логу должно быть понятно, почему тайл 1024, а не 2048 из пресета.
            if self.tuning is not None and self.tuning.changed():
                self.log_view.append_line(f"[BRM] hardware: {self.hardware.summary()}")
                self.log_view.append_line(f"[BRM] tuned for this machine: {self.tuning.summary()}")
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
        if event.kind == KIND_SAVED and event.saved_path:
            self._preview_frame(event.saved_path)
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
            if status != RUN_PAUSED:
                self._record_history(runner.plans[0].stats_path)
        paused = runner.is_paused()
        self.pause_button.setEnabled(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        self.stop_button.setEnabled(False)
        self.refresh_blender_status()

        if status != RUN_PAUSED:
            self.notifier.notify(
                "Render finished" if status == RUN_SUCCESS else "Render did not finish",
                f"{Path(runner.job.blend_path).stem if runner.job else 'Job'}: {text}",
                success=status == RUN_SUCCESS,
            )
        # Итог задачи придерживаем: очередь двинется дальше только после сборки видео,
        # иначе выключение ПК могло бы случиться раньше, чем ffmpeg допишет файл.
        self._pending_job_result = (status, done, total)
        if status == RUN_SUCCESS and self.video_panel.auto_build() and runner.plans:
            if self.build_video(runner.plans[0].output_path):
                return
        self._continue_after_job()

    def _continue_after_job(self) -> None:
        """Задача (и её видео, если было) завершена: двигаем очередь."""
        result = self._pending_job_result
        self._pending_job_result = None
        if result is not None:
            self._finish_queue_item(*result)

    # --- история ------------------------------------------------------------------------

    def _record_history(self, stats_path) -> None:
        """Запись в history.db не должна ронять завершение рендера — ловим всё."""
        try:
            self.history_store.record_from_stats_file(stats_path)
        except Exception as exc:  # noqa: BLE001 — намеренно широко, см. докстринг
            self.log_view.append_line(f"[BRM] SKIP history: {exc}")
            return
        # Обновляем всегда: линии прошлых прогонов нужны живому графику,
        # даже когда окно истории ни разу не открывали.
        self.refresh_history()

    # --- превью последнего кадра ---------------------------------------------------

    def show_preview(self) -> None:
        """Немодальное окно: во время рендера обновляется, иначе показывает последний кадр с диска."""
        if self._preview_window is None:
            self._preview_window = PreviewWindow(self)
        window = self._preview_window
        if window.current_path is None:
            self._show_last_frame_from_disk()
        window.show()
        window.raise_()
        window.activateWindow()

    def _preview_frame(self, path: str) -> None:
        """Кадр из строки Saved: — только если окно открыто и следит за рендером."""
        window = self._preview_window
        if window is None or window.isHidden() or not window.follows_render():
            return
        if is_previewable(path):
            window.show_frame(path)
        else:
            window.show_message(describe_unpreviewable(path), "warning")

    def _show_last_frame_from_disk(self) -> None:
        """Открыли приложение после ночного рендера — показываем, чем всё кончилось."""
        window = self._preview_window
        if window is None:
            return
        output_path = None
        if self.runner.plans:
            output_path = self.runner.plans[0].output_path
        else:
            job = self.project_panel.current_job()
            scene = None
            if job is not None and self.project is not None:
                scene = self.project.scene(job.scene) or self.project.default_scene()
            if job is not None and scene is not None:
                output_path = resolve_output_path(job, self.settings, scene.name)
        if not output_path:
            window.clear()
            return
        frame = latest_rendered_frame(output_path)
        if frame is None:
            unpreviewable = latest_rendered_frame(output_path, previewable_only=False)
            if unpreviewable is not None:
                window.show_message(describe_unpreviewable(unpreviewable), "warning")
            else:
                window.clear()
            return
        window.show_frame(frame, note="last frame on disk")

    def show_history(self) -> None:
        if self._history_dialog is None:
            self._history_dialog = HistoryDialog(self)
            view = self._history_dialog.view
            view.refresh_requested.connect(self.refresh_history)
            view.row_selected.connect(self._on_history_row_selected)
            view.reference_toggled.connect(self.toggle_reference_render)
            view.delete_requested.connect(self.delete_selected_render)
            view.clear_requested.connect(self.clear_render_history)
        self.refresh_history()
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()

    def refresh_history(self) -> None:
        if self._history_dialog is not None:
            self._history_dialog.view.set_entries(self.history_store.list_entries())
        self._refresh_live_chart_history()

    # --- график времени кадров ---------------------------------------------------

    @staticmethod
    def _entry_label(entry: HistoryEntry) -> str:
        scene = f" · {entry.scene}" if entry.scene else ""
        return f"{entry.project}{scene} · {entry.preset or 'no preset'}"

    def _history_points(self, limit: int = 12) -> list[tuple[int, str, list[tuple[int, float]]]]:
        """(id, подпись, точки) от новых к старым. Читается из stats-файлов рядом с логом."""
        result = []
        for entry in self.history_store.list_entries(limit=limit):
            if entry.id is None:
                continue
            result.append((entry.id, self._entry_label(entry), read_frame_times(entry.stats_path)))
        return result

    def _refresh_live_chart_history(self) -> None:
        """Прошлые прогоны под линией текущего рендера в панели прогресса."""
        history = self._history_points()
        series = build_series(None, history, reference_id=self.settings.reference_render_id)
        self.progress_panel.set_history_series(series)

    def _on_history_row_selected(self, stats_path: str) -> None:
        if self._history_dialog is None:
            return
        view = self._history_dialog.view
        entry = view.selected_entry()
        selected = read_frame_times(stats_path) if stats_path else None
        history = [item for item in self._history_points() if entry is None or item[0] != entry.id]
        series = build_series(selected, history, reference_id=self.settings.reference_render_id)
        if selected and entry is not None:
            # Выбранная строка — «текущая» линия графика: синим, поверх остальных.
            series[0] = replace(series[0], label=self._entry_label(entry))
        view.show_series(series)
        is_reference = entry is not None and entry.id == self.settings.reference_render_id
        view.set_reference_state(is_reference=is_reference, has_selection=entry is not None)

    def toggle_reference_render(self) -> None:
        """Эталон рисуется золотым на каждом графике и не вытесняется возрастом."""
        if self._history_dialog is None:
            return
        entry = self._history_dialog.view.selected_entry()
        if entry is None or entry.id is None:
            return
        current = self.settings.reference_render_id
        self.settings.reference_render_id = None if current == entry.id else entry.id
        self._store.save(self.settings)
        self.refresh_history()
        self._on_history_row_selected(entry.stats_path)

    def delete_selected_render(self) -> None:
        if self._history_dialog is None:
            return
        entry = self._history_dialog.view.selected_entry()
        if entry is None or entry.id is None:
            return
        answer = QMessageBox.question(self, "Delete render", f"Remove {self._entry_label(entry)} from the history?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.history_store.delete_entry(entry.id)
        if self.settings.reference_render_id == entry.id:
            self.settings.reference_render_id = None
            self._store.save(self.settings)
        self.refresh_history()

    def clear_render_history(self) -> None:
        answer = QMessageBox.question(self, "Clear history", "Erase the whole render history?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.history_store.clear()
        if self.settings.reference_render_id is not None:
            self.settings.reference_render_id = None
            self._store.save(self.settings)
        self.refresh_history()
        self.log_view.set_status(f"History cleared, {removed} render(s) removed", "ok")

    # --- видео ------------------------------------------------------------------------

    def refresh_ffmpeg_status(self) -> None:
        status = validate_ffmpeg_path(self.settings.ffmpeg_path)
        self.video_panel.set_available(status.ok, status.reason)
        if status.ok:
            self.video_panel.set_status("Ready. Frames are rendered as a sequence, video is a separate step")

    def current_video_preset(self) -> VideoPreset | None:
        return find_video_preset(self.video_presets, self.video_panel.current_preset_name())

    def build_video(self, output_path: str | None = None) -> bool:
        """Собирает секвенцию в видео. ``output_path`` — папка кадров прошедшего рендера."""
        if self.video_process.is_running():
            return False
        status = validate_ffmpeg_path(self.settings.ffmpeg_path)
        if not status.ok:
            self.video_panel.set_status(status.reason, "error")
            return False
        preset = self.current_video_preset()
        if preset is None:
            self.video_panel.set_status("No video preset selected", "error")
            return False
        source = output_path or (self.runner.plans[0].output_path if self.runner.plans else None)
        if source is None:
            job = self.project_panel.current_job()
            source = job.output_template if job else None
        if not source:
            self.video_panel.set_status("Render a sequence first", "error")
            return False
        try:
            sequence = find_sequence(source)
        except FfmpegError as exc:
            self.video_panel.set_status(str(exc), "error")
            return False

        fps = 25.0
        scene = self.project.default_scene() if self.project else None
        if scene is not None and scene.fps:
            fps = scene.fps
        output_file = default_output_file(sequence, preset)
        argv = build_ffmpeg_argv(status.path, sequence, preset, output_file, fps=fps)
        self.video_panel.set_running(True, sequence.frame_count)
        self.video_panel.set_status(f"Encoding {sequence.frame_count} frame(s) to {output_file.name}…", "")
        self.log_view.append_line(f"[BRM] ffmpeg: {' '.join(argv)}")
        self.video_process.start(argv, total_frames=sequence.frame_count, output_file=output_file)
        return True

    def stop_video(self) -> None:
        if self.video_process.is_running():
            self.video_process.stop()

    def _on_video_line(self, line: str) -> None:
        if line.strip():
            self.log_view.append_line(line)

    def _on_video_progress(self, progress) -> None:
        self.video_panel.update_progress(progress)

    def _on_video_finished(self, exit_code: int, status: str) -> None:
        process = self.video_process
        text = describe_result(status, exit_code, process.progress, process.output_file)
        self.video_panel.set_running(False)
        self.video_panel.set_status(text, "ok" if status == VIDEO_SUCCESS else "error")
        self.log_view.append_line(f"[BRM] {text}")
        if status == VIDEO_SUCCESS and process.output_file is not None:
            self.notifier.notify("Video ready", str(process.output_file))
        else:
            self.notifier.notify("Video assembly failed", text, success=False)
        self._continue_after_job()

    # --- выключение ПК ------------------------------------------------------------------

    def maybe_shutdown(self) -> None:
        """Автовыключение после очереди: сначала плашка с отменой, потом сам shutdown."""
        if not self.settings.shutdown_after_queue or self._shutdown_pending:
            return
        result = schedule_shutdown(SHUTDOWN_DELAY_S)
        if not result.ok:
            self.log_view.append_line(f"[BRM] SKIP shutdown: {result.message}")
            return
        self._shutdown_pending = True
        self.shutdown_banner.set_message(f"The PC will shut down in {SHUTDOWN_DELAY_S} s after the render queue")
        self.shutdown_banner.show()
        self.notifier.notify("Shutdown scheduled", f"The PC shuts down in {SHUTDOWN_DELAY_S} s. Open BRM to cancel")

    def cancel_shutdown(self) -> None:
        result = cancel_shutdown()
        self._shutdown_pending = False
        self.shutdown_banner.hide()
        self.log_view.append_line(f"[BRM] {result.message or 'Shutdown cancel failed'}")

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

    def _after_queue_finished(self) -> None:
        """Очередь дошла до конца: уведомление и, если просили, выключение ПК."""
        if self._queue_finished_pending:
            self._queue_finished_pending = False
            self.notifier.notify("Queue finished", "All queued jobs are done")
        self.maybe_shutdown()

    def _run_next_queue_item(self) -> None:
        item = self.queue.next_pending()
        if item is None:
            self._queue_running = False
            self._active_queue_item = None
            self._save_queue()
            self.log_view.set_status("Queue finished", "ok")
            self._queue_finished_pending = True
            self._after_queue_finished()
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
            was_running, self._queue_running = self._queue_running, False
            self._save_queue()
            if was_running:  # очередь оборвана Стопом: уведомление о конце не шлём
                self._queue_finished_pending = False

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
            self._apply_settings(dialog.result_settings())

    def maybe_show_onboarding(self) -> None:
        """Модальный онбординг первого запуска (раздел 4.1, п.3): сразу просит путь к Blender.

        Флаг ставится при любом закрытии, не только по OK — иначе диалог будет
        всплывать на каждом старте, а для этого уже есть постоянный баннер.
        """
        if self.settings.onboarding_seen:
            return
        dialog = OnboardingDialog(self.settings, self)
        dialog.focus_blender_path()
        result = self.settings
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.result_settings()
        self._apply_settings(result.model_copy(update={"onboarding_seen": True}))

    def _apply_settings(self, settings: AppSettings) -> None:
        """Общий хвост после диалога настроек — и обычного, и онбординга."""
        self.settings = settings
        self._store.save(self.settings)
        apply_theme(QApplication.instance(), self.settings.theme)
        self.project_panel.set_default_output_dir(self.settings.default_output_dir or "")
        self.notifier.enabled = self.settings.notifications
        self.refresh_ffmpeg_status()
        self.reprobe_blender()

    def save_video_choice(self) -> None:
        """Пресет кодека и автосборка запоминаются между запусками."""
        name = self.video_panel.current_preset_name() or self.settings.last_video_preset
        auto = self.video_panel.auto_build()
        if name != self.settings.last_video_preset or auto != self.settings.auto_build_video:
            self.settings = self.settings.model_copy(update={"last_video_preset": name, "auto_build_video": auto})
            self._store.save(self.settings)

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
        self.save_video_choice()
        if self.runner.is_running():
            self.runner.stop()
        if self.video_process.is_running():
            self.video_process.stop()
        self.notifier.hide()
        super().closeEvent(event)

    def event(self, event: QEvent) -> bool:
        # Путь мог протухнуть, пока окно было в фоне (переустановили Blender).
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "render_button"):
            self.refresh_blender_status()
        return super().event(event)
