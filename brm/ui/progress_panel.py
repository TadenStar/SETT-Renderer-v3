"""Панель «Progress» (раздел 4.4 спеки): два прогресс-бара, строка статуса, sparkline.

Только отображение: числа приходят готовыми из core.render_stats.
"""
from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QLabel, QProgressBar, QVBoxLayout, QWidget

from brm.core.render_stats import RenderProgress, format_duration, format_memory
from brm.ui.sparkline import Sparkline
from brm.ui.theme import set_role


class ProgressPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Progress", parent)
        self.status_label = QLabel("Waiting to start", self)
        self.detail_label = QLabel("", self)
        self.detail_label.setWordWrap(True)
        set_role(self.detail_label, "muted")
        self.hint_label = QLabel("", self)
        self.hint_label.setWordWrap(True)
        set_role(self.hint_label, "warning")
        self.hint_label.hide()

        self.frames_bar = QProgressBar(self)
        self.frames_bar.setFormat("%v / %m frames")
        self.samples_bar = QProgressBar(self)
        self.samples_bar.setFormat("sample %v / %m")
        self.sparkline = Sparkline(self)
        self.sparkline.setToolTip("Frame time, left to right")

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.frames_bar)
        layout.addWidget(self.samples_bar)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.sparkline)
        self.set_idle()

    def set_idle(self) -> None:
        self.status_label.setText("Waiting to start")
        set_role(self.status_label, "muted")
        self.detail_label.setText("")
        self.hint_label.hide()
        for bar in (self.frames_bar, self.samples_bar):
            bar.setRange(0, 1)
            bar.setValue(0)
        self.sparkline.clear()

    def set_running(self, total_frames: int) -> None:
        self.status_label.setText(f"Starting Blender, {total_frames} frame(s) queued…")
        set_role(self.status_label, "")
        self.detail_label.setText("")
        self.hint_label.hide()
        self.frames_bar.setRange(0, max(total_frames, 1))
        self.frames_bar.setValue(0)
        self.samples_bar.setRange(0, 1)
        self.samples_bar.setValue(0)
        self.sparkline.clear()

    def update_progress(self, progress: RenderProgress, elapsed_s: float, note: str = "") -> None:
        total = max(progress.frames_total, 1)
        self.frames_bar.setRange(0, total)
        self.frames_bar.setValue(min(progress.frames_done_count, total))
        if progress.samples_total:
            self.samples_bar.setRange(0, progress.samples_total)
            self.samples_bar.setValue(min(progress.sample or 0, progress.samples_total))
        else:
            self.samples_bar.setRange(0, 1)
            self.samples_bar.setValue(0)

        parts = [f"Frame {progress.frames_done_count} / {progress.frames_total}"]
        if progress.current_frame is not None:
            parts.append(f"rendering {progress.current_frame}")
        if progress.samples_total:
            parts.append(f"sample {progress.sample or 0} / {progress.samples_total}")
        if note:
            parts.append(note)
        self.status_label.setText(" · ".join(parts))
        set_role(self.status_label, "")

        details = [f"Elapsed {format_duration(elapsed_s)}"]
        eta = progress.eta_seconds()
        details.append(f"ETA {format_duration(eta)}" if eta is not None else "ETA after the first frame")
        times = progress.frame_times()
        if times:
            details.append(f"Last frame {times[-1][1]:.1f} s")
        if progress.mem_mb is not None:
            details.append(f"Mem {format_memory(progress.mem_mb)}")
        if progress.peak_mb is not None:
            details.append(f"Peak {format_memory(progress.peak_mb)}")
        if progress.engine:
            details.append(progress.engine)
        self.detail_label.setText(" · ".join(details))
        self.sparkline.set_values(times)

    def set_finished(self, text: str, role: str = "", hint: str | None = None) -> None:
        self.status_label.setText(text)
        set_role(self.status_label, role)
        if hint:
            self.hint_label.setText(hint)
            self.hint_label.show()
        else:
            self.hint_label.hide()
