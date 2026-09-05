"""Панель «Project» (раздел 4.2 спеки): файл, сводка, сцена, диапазон кадров, вывод.

Только отображение: чтение файла делает core.project_probe через главное окно,
подсчёт кадров и раскрытие шаблона — функции core, панель их лишь вызывает.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from brm.core.frame_range import FrameRange, FrameRangeMode, describe_frames, resolve_frames
from brm.core.models import DEFAULT_OUTPUT_TEMPLATE, RenderJob, expand_output_template
from brm.core.project_probe import ProjectInfo, SceneInfo, file_version_str
from brm.ui.theme import set_role

_FRAME_MODES = [
    (FrameRangeMode.FROM_FILE, "From file"),
    (FrameRangeMode.MANUAL, "Manual range"),
    (FrameRangeMode.SINGLE, "Single frame"),
    (FrameRangeMode.LIST, "Frame list"),
]
_FRAME_LIMIT = 1_000_000


def blend_paths_from_mime(mime) -> list[str]:
    """Пути .blend из перетаскиваемых URL; пусто, если тащат что-то другое."""
    if not mime.hasUrls():
        return []
    return [
        url.toLocalFile()
        for url in mime.urls()
        if url.isLocalFile() and url.toLocalFile().lower().endswith(".blend")
    ]


class ProjectPanel(QGroupBox):
    file_requested = Signal(str)
    safety_requested = Signal()
    analysis_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Project", parent)
        self.setAcceptDrops(True)
        self._info: ProjectInfo | None = None
        self._scene: SceneInfo | None = None
        self._default_output_dir = ""

        # --- файл -------------------------------------------------------------
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("Drop a .blend file here or browse…")
        self.path_edit.returnPressed.connect(self._request_typed_path)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self.browse)
        self.recent_button = QToolButton(self)
        self.recent_button.setText("Recent")
        self.recent_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.recent_menu = QMenu(self.recent_button)
        self.recent_button.setMenu(self.recent_menu)
        self.recent_button.setEnabled(False)

        file_row = QHBoxLayout()
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(self.recent_button)

        self.summary_label = QLabel("No project loaded.", self)
        self.summary_label.setWordWrap(True)
        set_role(self.summary_label, "muted")
        self.warnings_label = QLabel(self)
        self.warnings_label.setWordWrap(True)
        set_role(self.warnings_label, "warning")
        self.warnings_label.hide()

        # --- сцена ------------------------------------------------------------
        self.scene_combo = QComboBox(self)
        self.scene_combo.currentTextChanged.connect(self._on_scene_changed)
        self.view_layer_combo = QComboBox(self)
        # Камера видна в строке с диапазоном кадров: отдельная строка её не стоит.
        self.camera_label = QLabel("—", self)
        self.camera_label.setWordWrap(True)
        self.camera_label.hide()

        # --- диапазон кадров ----------------------------------------------------
        self.mode_combo = QComboBox(self)
        for mode, title in _FRAME_MODES:
            self.mode_combo.addItem(title, mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.start_spin = self._spin()
        self.end_spin = self._spin()
        self.frame_spin = self._spin()
        self.step_spin = QSpinBox(self)
        self.step_spin.setRange(1, 10_000)
        self.list_edit = QLineEdit(self)
        self.list_edit.setPlaceholderText("1,5,10..20")
        self.file_range_label = QLabel("—", self)

        self.range_stack = QStackedWidget(self)
        self.range_stack.addWidget(self.file_range_label)  # FROM_FILE
        manual = QWidget(self)
        manual_row = QHBoxLayout(manual)
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.addWidget(QLabel("Start", manual))
        manual_row.addWidget(self.start_spin, 1)
        manual_row.addWidget(QLabel("End", manual))
        manual_row.addWidget(self.end_spin, 1)
        self.range_stack.addWidget(manual)  # MANUAL
        self.range_stack.addWidget(self.frame_spin)  # SINGLE
        self.range_stack.addWidget(self.list_edit)  # LIST

        range_row = QHBoxLayout()
        range_row.addWidget(self.mode_combo)
        range_row.addWidget(self.range_stack, 1)
        range_row.addWidget(QLabel("Step", self))
        range_row.addWidget(self.step_spin)

        self.frames_label = QLabel("—", self)
        self.frames_label.setWordWrap(True)

        for widget in (self.start_spin, self.end_spin, self.frame_spin, self.step_spin):
            widget.valueChanged.connect(self._update_frames_summary)
        self.list_edit.textChanged.connect(self._update_frames_summary)

        # --- вывод --------------------------------------------------------------
        self.output_edit = QLineEdit(DEFAULT_OUTPUT_TEMPLATE, self)
        self.output_edit.textChanged.connect(self._update_output_preview)
        output_browse = QPushButton("Folder…", self)
        output_browse.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_browse)
        self.output_preview = QLabel("—", self)
        self.output_preview.setWordWrap(True)
        set_role(self.output_preview, "muted")

        # --- защита от падений (M5) --------------------------------------------------
        self.resume_check = QCheckBox("Skip frames already on disk", self)
        self.resume_check.setChecked(True)
        self.resume_check.setToolTip("Resume: only frames missing from the output folder are rendered")
        self.min_kb_spin = QSpinBox(self)
        self.min_kb_spin.setRange(0, 1_000_000)
        self.min_kb_spin.setSuffix(" KB")
        self.min_kb_spin.setSpecialValueText("any size")
        self.min_kb_spin.setToolTip("Re-render existing frames smaller than this: catches empty or broken files")
        self.chunk_spin = QSpinBox(self)
        self.chunk_spin.setRange(0, 100_000)
        self.chunk_spin.setSpecialValueText("preset")
        self.chunk_spin.setToolTip(
            "Frames per Blender process. Memory leaks do not pile up and a crash costs one chunk, "
            "at the price of rebuilding the BVH per chunk. 'preset' takes the value from the preset"
        )
        self.analyze_button = QPushButton("Analyze…", self)
        self.analyze_button.setToolTip("Count objects, triangles and instances in this scene")
        self.analyze_button.clicked.connect(self.analysis_requested)
        self.analyze_button.setEnabled(False)

        self.safety_button = QPushButton("Safety…", self)
        self.safety_button.setToolTip("Skipping finished frames, minimum frame size and chunk size")
        self.safety_button.clicked.connect(self.safety_requested)

        # Виджеты живут здесь (их читает current_job), а показываются в отдельном
        # окне: в работе эти три поля не трогают, а место на главном экране занимали.
        self.safety_widget = QWidget()
        safety_form = QFormLayout(self.safety_widget)
        safety_form.setContentsMargins(0, 0, 0, 0)
        min_kb_row = QHBoxLayout()
        min_kb_row.addWidget(self.min_kb_spin)
        min_kb_row.addStretch(1)
        chunk_row = QHBoxLayout()
        chunk_row.addWidget(self.chunk_spin)
        chunk_row.addStretch(1)
        safety_form.addRow(self.resume_check)
        safety_form.addRow("Re-render below:", min_kb_row)
        safety_form.addRow("Chunk size:", chunk_row)

        # --- компоновка ---------------------------------------------------------
        self.form = QWidget(self)
        form = QFormLayout(self.form)
        form.setContentsMargins(0, 0, 0, 0)
        # Строки сцены и слоя прячутся, когда выбирать не из чего: в файле с одной
        # сценой и одним слоем они только занимали место (отзыв с реальной задачи).
        self.scene_label = QLabel("Scene:", self)
        self.view_layer_label = QLabel("View layer:", self)
        form.addRow(self.scene_label, self.scene_combo)
        form.addRow(self.view_layer_label, self.view_layer_combo)
        form.addRow("Frames:", range_row)
        form.addRow("", self.frames_label)
        form.addRow("Output:", output_row)
        form.addRow("", self.output_preview)
        self.form.setEnabled(False)

        file_row.addWidget(self.analyze_button)
        file_row.addWidget(self.safety_button)

        layout = QVBoxLayout(self)
        layout.addLayout(file_row)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.warnings_label)
        layout.addWidget(self.form)
        layout.addStretch(1)

        self._on_mode_changed(0)

    # --- публичное API ---------------------------------------------------------

    def set_recent(self, paths: list[str]) -> None:
        self.recent_menu.clear()
        for path in paths:
            action = QAction(path, self.recent_menu)
            action.triggered.connect(lambda _checked=False, p=path: self.file_requested.emit(p))
            self.recent_menu.addAction(action)
        self.recent_button.setEnabled(bool(paths))

    def set_default_output_dir(self, path: str) -> None:
        self._default_output_dir = path
        self._update_output_preview()

    def set_loading(self, path: str) -> None:
        self.path_edit.setText(path)
        self.summary_label.setText(f"Reading {Path(path).name}…")
        set_role(self.summary_label, "muted")
        self.warnings_label.hide()
        self.form.setEnabled(False)
        self.analyze_button.setEnabled(False)

    def set_error(self, message: str) -> None:
        self.summary_label.setText(message)
        set_role(self.summary_label, "error")
        self.warnings_label.hide()
        self.form.setEnabled(False)
        self.analyze_button.setEnabled(False)

    def set_project(self, info: ProjectInfo, warnings: list[str]) -> None:
        self._info = info
        self.path_edit.setText(info.file_path)
        self.summary_label.setText(self._summary_text(info))
        set_role(self.summary_label, "")
        if warnings:
            self.warnings_label.setText("\n".join(f"⚠ {w}" for w in warnings))
            self.warnings_label.show()
        else:
            self.warnings_label.hide()

        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        self.scene_combo.addItems([s.name for s in info.scenes])
        self._show_row(self.scene_label, self.scene_combo, len(info.scenes) > 1)
        default = info.default_scene()
        if default is not None:
            self.scene_combo.setCurrentText(default.name)
        self.scene_combo.blockSignals(False)
        self._on_scene_changed(self.scene_combo.currentText())
        self.form.setEnabled(bool(info.scenes))
        self.analyze_button.setEnabled(bool(info.scenes))

    def current_job(self) -> RenderJob | None:
        """Задача из текущих значений виджетов; None, если проект не загружен."""
        if self._info is None or self._scene is None:
            return None
        return RenderJob(
            blend_path=self._info.file_path,
            scene=self._scene.name,
            view_layer=self.view_layer_combo.currentText() or None,
            frame_range=self.current_frame_range(),
            output_template=self.output_edit.text().strip() or DEFAULT_OUTPUT_TEMPLATE,
            resume=self.resume_check.isChecked(),
            min_frame_kb=self.min_kb_spin.value(),
            chunk_size=self.chunk_spin.value() or None,
        )

    def current_frame_range(self) -> FrameRange:
        return FrameRange(
            mode=self.mode_combo.currentData(),
            start=self.start_spin.value(),
            end=self.end_spin.value(),
            step=self.step_spin.value(),
            frame=self.frame_spin.value(),
            frames_text=self.list_edit.text(),
        )

    # --- drag & drop -------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 — имя из Qt
        if blend_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 — имя из Qt
        paths = blend_paths_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.file_requested.emit(paths[0])

    # --- слоты -------------------------------------------------------------------

    def browse(self) -> None:
        start = self.path_edit.text().strip()
        start_dir = str(Path(start).parent) if start else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open .blend file", start_dir, "Blender files (*.blend);;All files (*)"
        )
        if path:
            self.file_requested.emit(str(Path(path)))

    def _request_typed_path(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self.file_requested.emit(path)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output folder", self._default_output_dir)
        if path:
            self.output_edit.setText(str(Path(path) / "{project}" / "{scene}" / "####"))

    def _on_scene_changed(self, name: str) -> None:
        self._scene = self._info.scene(name) if self._info is not None else None
        scene = self._scene
        self.view_layer_combo.clear()
        if scene is None:
            self.camera_label.setText("—")
            self.file_range_label.setText("—")
            self._update_frames_summary()
            self._update_output_preview()
            return
        self.view_layer_combo.addItems([vl.name for vl in scene.view_layers])
        self._show_row(self.view_layer_label, self.view_layer_combo, len(scene.view_layers) > 1)
        active = scene.active_camera or "none"
        others = [c for c in scene.cameras if c != scene.active_camera]
        suffix = f" (also: {', '.join(others)})" if others else ""
        self.camera_label.setText(f"{active}{suffix}")
        self._update_frames_summary()

        self.file_range_label.setText(
            f"{scene.frame_start}..{scene.frame_end}, step {scene.frame_step} in file"
        )
        for spin, value in (
            (self.start_spin, scene.frame_start),
            (self.end_spin, scene.frame_end),
            (self.frame_spin, scene.frame_current),
            (self.step_spin, max(scene.frame_step, 1)),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._update_frames_summary()
        self._update_output_preview()

    def _on_mode_changed(self, index: int) -> None:
        self.range_stack.setCurrentIndex(index)
        mode = self.mode_combo.currentData()
        self.step_spin.setEnabled(mode in (FrameRangeMode.FROM_FILE, FrameRangeMode.MANUAL))
        self._update_frames_summary()

    @staticmethod
    def _show_row(label: QLabel, widget: QWidget, visible: bool) -> None:
        """Показывает или прячет строку формы целиком, вместе с подписью."""
        label.setVisible(visible)
        widget.setVisible(visible)

    def _update_frames_summary(self) -> None:
        scene = self._scene
        if self._info is None:
            self.frames_label.setText("—")
            set_role(self.frames_label, "")
            return
        try:
            frames = resolve_frames(
                self.current_frame_range(),
                scene_start=scene.frame_start if scene else None,
                scene_end=scene.frame_end if scene else None,
            )
        except ValueError as exc:
            self.frames_label.setText(str(exc))
            set_role(self.frames_label, "error")
            return
        text = describe_frames(frames)
        if scene is not None and scene.fps:
            text += f" · {len(frames) / scene.fps:.1f} s at {scene.fps:g} fps"
        camera = self.camera_label.text()
        if scene is not None and camera and camera != "—":
            text += f" · camera {camera}"
        self.frames_label.setText(text)
        set_role(self.frames_label, "")

    def _update_output_preview(self) -> None:
        if self._info is None or self._scene is None:
            self.output_preview.setText("—")
            return
        expanded = expand_output_template(
            self.output_edit.text().strip() or DEFAULT_OUTPUT_TEMPLATE,
            output_dir=self._default_output_dir or str(Path(self._info.file_path).parent),
            project=Path(self._info.file_path).stem,
            scene=self._scene.name,
        )
        self.output_preview.setText(expanded)

    # --- утилиты -------------------------------------------------------------------

    def _spin(self) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(-_FRAME_LIMIT, _FRAME_LIMIT)
        return spin

    @staticmethod
    def _summary_text(info: ProjectInfo) -> str:
        scene = info.default_scene()
        parts = [f"Saved with Blender {file_version_str(info.saved_with_version)}"]
        parts.append(f"{len(info.scenes)} scene{'s' if len(info.scenes) != 1 else ''}")
        if scene is not None:
            width, height = scene.final_resolution
            parts.append(f"{scene.engine}")
            parts.append(f"{width}×{height}")
            parts.append(f"{scene.frame_count} frames")
            if scene.fps:
                parts.append(f"{scene.fps:g} fps")
            if scene.file_format:
                parts.append(scene.file_format)
        return " · ".join(parts)
