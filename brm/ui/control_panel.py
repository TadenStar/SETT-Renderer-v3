"""Карточка «Progress» внизу окна: три кнопки управления и полосы прогресса.

Кнопки переехали сюда из верхней панели по макету Павла: то, чем управляют
рендером, и то, что показывает его ход, стоит держать рядом. Вся строка
состояния собрана в текст на самой полосе — числа приходят готовыми из
``core.render_stats``, панель их только раскладывает.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from brm.core.render_stats import RenderProgress, format_duration, format_memory
from brm.ui.icons import glyph_icon
from brm.ui.theme import current_theme, set_role, tokens_for

KERNEL_HINT_MARK = "render kernels"
KERNEL_HINT = (
    "Building render kernels — the first frame of a new scene can take minutes. "
    "Later renders of the same materials reuse the cache."
)
IDLE_TEXT = "Waiting to start"


class ControlPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Progress", parent)
        self.render_button = QPushButton("Render", self)
        self.render_button.setObjectName("primaryButton")  # единственная акцентная кнопка
        self.render_button.setToolTip("Start rendering the current project")
        self.pause_button = QPushButton("Pause", self)
        self.pause_button.setObjectName("warnButton")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Pause after the current frame; Resume renders the remaining frames")
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Stop after terminate; kill if Blender ignores it for 5 s. Stops the queue too")

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        for button in (self.render_button, self.pause_button, self.stop_button):
            button.setMinimumHeight(38)
            buttons.addWidget(button, 1)

        self.frames_bar = QProgressBar(self)
        self.samples_bar = QProgressBar(self)
        # Тонкая полоса без текста: сэмплы уже названы в строке над ней.
        self.samples_bar.setObjectName("thinBar")
        self.samples_bar.setTextVisible(False)

        self.hint_label = QLabel("", self)
        self.hint_label.setWordWrap(True)
        set_role(self.hint_label, "warning")
        self.hint_label.hide()

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(buttons)
        layout.addWidget(self.frames_bar)
        layout.addWidget(self.samples_bar)
        layout.addWidget(self.hint_label)

        self.refresh_icons()
        self.set_idle()

    # --- иконки ------------------------------------------------------------------

    def refresh_icons(self) -> None:
        """Глифы рисуются цветом текста кнопки, поэтому их обновляют после смены темы."""
        tokens = tokens_for(current_theme(None) or "dark")
        for button, glyph, key in (
            (self.render_button, "play", "primary_text"),
            (self.pause_button, "pause", "warn_button_text"),
            (self.stop_button, "stop", "danger_text"),
        ):
            button.setIcon(glyph_icon(glyph, tokens[key]))

    # --- состояние ---------------------------------------------------------------

    def status_text(self) -> str:
        return self.frames_bar.format()

    def set_status(self, text: str) -> None:
        self.frames_bar.setFormat(text)

    def set_idle(self) -> None:
        self.hint_label.hide()
        for bar in (self.frames_bar, self.samples_bar):
            bar.setRange(0, 1)
            bar.setValue(0)
        self.set_status(IDLE_TEXT)

    def set_running(self, total_frames: int) -> None:
        self.hint_label.hide()
        self.frames_bar.setRange(0, max(total_frames, 1))
        self.frames_bar.setValue(0)
        self.samples_bar.setRange(0, 1)
        self.samples_bar.setValue(0)
        self.set_status(f"Starting Blender · {total_frames} frame(s) queued…")

    def update_progress(self, progress: RenderProgress, elapsed_s: float, note: str = "") -> None:
        total = max(progress.frames_total, 1)
        done = min(progress.frames_done_count, total)
        self.frames_bar.setRange(0, total)
        self.frames_bar.setValue(done)
        if progress.samples_total:
            self.samples_bar.setRange(0, progress.samples_total)
            self.samples_bar.setValue(min(progress.sample or 0, progress.samples_total))
        else:
            self.samples_bar.setRange(0, 1)
            self.samples_bar.setValue(0)

        parts = [f"{done} / {progress.frames_total} frames", f"{round(100 * done / total)}%"]
        if progress.current_frame is not None:
            parts.append(f"frame {progress.current_frame}")
        if progress.samples_total:
            parts.append(f"sample {progress.sample or 0}/{progress.samples_total}")
        parts.append(format_duration(elapsed_s))
        if progress.peak_mb is not None:
            parts.append(f"VRAM {format_memory(progress.peak_mb)}")
        if note:
            parts.append(note)
        self.set_status(" · ".join(parts))

        # Компиляция ядер занимает минуты на свежих материалах: в это время нет
        # ни сэмплов, ни кадров, и без подсказки рендер выглядит зависшим.
        # На тестовой сцене из 40 материалов это заняло 4 минуты 31 секунду
        # против 2 секунд самого кадра.
        if progress.compiling_kernels:
            self.hint_label.setText(KERNEL_HINT)
            set_role(self.hint_label, "warning")
            self.hint_label.show()
        elif KERNEL_HINT_MARK in self.hint_label.text():
            # Прячем только свою подсказку: подсказку о причине падения ставит
            # set_finished, и затирать её нельзя.
            self.hint_label.setText("")
            self.hint_label.hide()

    def set_finished(self, text: str, role: str = "", hint: str | None = None) -> None:
        self.set_status(text)
        if hint:
            self.hint_label.setText(hint)
            set_role(self.hint_label, "error" if role == "error" else "warning")
            self.hint_label.show()
        elif role == "error":
            # Красную строку из бара не покрасить, поэтому провал повторяем подсказкой.
            self.hint_label.setText(text)
            set_role(self.hint_label, "error")
            self.hint_label.show()
        else:
            self.hint_label.hide()
