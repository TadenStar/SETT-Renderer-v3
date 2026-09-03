"""Панель «Video» (раздел 4.8 спеки): сборка секвенции в видео внешним ffmpeg.

Только отображение: команду собирает core.ffmpeg, запускает core.video_runner.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from brm.core.ffmpeg import FfmpegProgress, VideoPreset
from brm.ui.theme import set_role


class VideoPanel(QGroupBox):
    build_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Video", parent)
        self._presets: list[VideoPreset] = []

        self.preset_combo = QComboBox(self)
        self.preset_combo.currentIndexChanged.connect(self._show_description)
        self.auto_check = QCheckBox("Assemble after a successful render", self)
        self.auto_check.setToolTip("Frames are always rendered as a sequence; the video is a separate step")
        self.build_button = QPushButton("Build video now", self)
        self.build_button.clicked.connect(self.build_requested)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested)

        top = QHBoxLayout()
        top.addWidget(self.preset_combo, 1)
        top.addWidget(self.build_button)
        top.addWidget(self.stop_button)

        self.description_label = QLabel("", self)
        self.description_label.setWordWrap(True)
        # Описание пресета длинное: даём ему расти, иначе прогресс-бар его подрезает.
        self.description_label.setMinimumHeight(34)
        self.description_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        set_role(self.description_label, "muted")
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFormat("%v / %m frames")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_label = QLabel("ffmpeg is not set", self)
        self.status_label.setWordWrap(True)
        set_role(self.status_label, "muted")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.auto_check)
        layout.addWidget(self.description_label, 1)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)

    # --- публичное API ---------------------------------------------------------

    def set_presets(self, presets: list[VideoPreset], current: str | None = None) -> None:
        self._presets = list(presets)
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._presets:
            self.preset_combo.addItem(preset.name, preset.name)
        index = self.preset_combo.findData(current) if current else -1
        self.preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.preset_combo.blockSignals(False)
        self._show_description()

    def current_preset_name(self) -> str | None:
        return self.preset_combo.currentData()

    def auto_build(self) -> bool:
        return self.auto_check.isChecked()

    def set_auto_build(self, value: bool) -> None:
        self.auto_check.setChecked(value)

    def set_available(self, available: bool, reason: str = "") -> None:
        """ffmpeg не задан или протух — панель гаснет с объяснением."""
        for widget in (self.preset_combo, self.auto_check, self.build_button):
            widget.setEnabled(available)
        if not available:
            self.set_status(reason or "ffmpeg is not set: pick it in File → Settings", "muted")

    def set_running(self, running: bool, total_frames: int = 0) -> None:
        self.build_button.setEnabled(not running and self.preset_combo.isEnabled())
        self.stop_button.setEnabled(running)
        if running:
            self.progress_bar.setRange(0, max(total_frames, 1))
            self.progress_bar.setValue(0)

    def update_progress(self, progress: FfmpegProgress) -> None:
        if progress.total_frames:
            self.progress_bar.setRange(0, progress.total_frames)
        self.progress_bar.setValue(progress.frame)
        parts = [f"Encoding frame {progress.frame}"]
        if progress.total_frames:
            parts[0] += f" / {progress.total_frames}"
        if progress.speed:
            parts.append(f"{progress.speed:g}x")
        if progress.time:
            parts.append(progress.time)
        self.set_status(" · ".join(parts), "")

    def set_status(self, text: str, role: str = "muted") -> None:
        self.status_label.setText(text)
        set_role(self.status_label, role)

    def reset_progress(self) -> None:
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    # --- слоты -------------------------------------------------------------------

    def _show_description(self) -> None:
        name = self.current_preset_name()
        preset = next((p for p in self._presets if p.name == name), None)
        self.description_label.setText(preset.description if preset else "")
