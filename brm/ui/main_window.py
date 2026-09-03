"""Главное окно. Только отображение: статус Blender и проекта спрашиваем у core.

Пробы выполняются в фоне (``ui.workers``), результаты приходят сигналами.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
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
from brm.core.project_probe import ProjectInfo, probe_project, project_warnings
from brm.core.render_plan import RenderPlan, build_render_plan
from brm.core.runner import STATUS_FAILED, STATUS_STOPPED, STATUS_SUCCESS, RenderProcess
from brm.core.storage import SettingsStore, cache_dir, tmp_dir, with_recent_project
from brm.ui.banner import WarningBanner
from brm.ui.log_view import LogView
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

        self.render_process = RenderProcess(self)
        self.render_process.line_received.connect(self._on_render_line)
        self.render_process.finished.connect(self._on_render_finished)
        self.current_plan: RenderPlan | None = None
        self._render_started_at = 0.0

        self.setWindowTitle(f"BRM — Blender Render Manager {__version__}")
        self.resize(1200, 760)
        self.setAcceptDrops(True)

        self._build_menu()
        self._build_central()
        self.project_panel.set_recent(self.settings.recent_projects)
        self.project_panel.set_default_output_dir(self.settings.default_output_dir or "")
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
        self.stop_button = QPushButton("Stop", top)
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setMinimumWidth(100)
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Stop after terminate; kill if Blender ignores it for 5 s")
        self.stop_button.clicked.connect(self.stop_render)
        top_layout.addWidget(self.stop_button, 0)
        root.addWidget(top)

        self.project_panel = ProjectPanel()
        self.project_panel.file_requested.connect(self.open_project)
        self.settings_form = SettingsForm()
        self.log_view = LogView()
        self.queue_view = QueueView()

        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.project_panel)
        left.addWidget(self.settings_form)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.log_view)
        right.addWidget(self.queue_view)
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
        self.refresh_blender_status()

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
        self.render_button.setEnabled(not self.render_process.is_running())
        self.render_button.setToolTip("Start rendering the current project")
        engines = ", ".join(caps.engines)
        self.blender_label.setText(
            f"Blender {caps.version_string} · Cycles device: {caps.best_cycles_device()} · {engines}"
        )
        self.blender_label.setToolTip(caps.blender_path)

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

    def _on_project_failed(self, task: FunctionTask, message: str) -> None:
        if task is not self._project_task:
            return
        self._project_task = None
        self._end_task()
        self.project = None
        self.project_panel.set_error(f"Could not read {Path(task.tag).name}: {message}")

    # --- рендер ------------------------------------------------------------------

    def start_render(self) -> None:
        if self.render_process.is_running():
            return
        if self.capabilities is None:
            self.log_view.set_status("Configure a working Blender first", "error")
            return
        job = self.project_panel.current_job()
        if job is None or self.project is None:
            self.log_view.set_status("Load a project first", "error")
            return
        try:
            plan = build_render_plan(job, self.capabilities, self.settings, self.project, tmp_dir=tmp_dir())
        except ValueError as exc:
            self.log_view.set_status(str(exc), "error")
            return
        self.current_plan = plan
        self.log_view.clear()
        self.log_view.set_command(plan.command_line)
        self.log_view.append_line(f"[BRM] output: {plan.output_path}")
        self.log_view.append_line(f"[BRM] log file: {plan.log_path}")
        self.log_view.set_status(f"Rendering {len(plan.frames)} frame(s)…", "muted")
        self._render_started_at = time.monotonic()
        self.render_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.render_process.start(plan)

    def stop_render(self) -> None:
        if self.render_process.is_running():
            self.log_view.set_status("Stopping…", "warning")
            self.render_process.stop()

    def _on_render_line(self, line: str) -> None:
        self.log_view.append_line(line)

    def _on_render_finished(self, exit_code: int, status: str) -> None:
        elapsed = time.monotonic() - self._render_started_at
        plan = self.current_plan
        frames = len(plan.frames) if plan else 0
        if status == STATUS_SUCCESS:
            self.log_view.set_status(f"Finished: {frames} frame(s) in {elapsed:.0f} s", "ok")
        elif status == STATUS_STOPPED:
            self.log_view.set_status(f"Stopped by user after {elapsed:.0f} s", "warning")
        elif status == STATUS_FAILED:
            self.log_view.set_status(f"Blender exited with code {exit_code} after {elapsed:.0f} s. See the log", "error")
        else:
            self.log_view.set_status("Blender crashed or could not start. See the log", "error")
        if plan is not None:
            self.log_view.append_line(f"[BRM] finished: status={status} exit_code={exit_code} log={plan.log_path}")
        self.stop_button.setEnabled(False)
        self.refresh_blender_status()

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
        if self.render_process.is_running():
            self.render_process.stop()
        super().closeEvent(event)

    def event(self, event: QEvent) -> bool:
        # Путь мог протухнуть, пока окно было в фоне (переустановили Blender).
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "render_button"):
            self.refresh_blender_status()
        return super().event(event)
