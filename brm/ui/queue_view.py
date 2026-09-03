"""Панель «Queue» (раздел 4.5 спеки): простой список задач и последовательный прогон.

Только отображение: модель и порядок — core.queue, запуск — главное окно.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from brm.core.frame_range import FrameRange, FrameRangeMode
from brm.core.queue import QueueItem
from brm.ui.theme import set_role

COLUMNS = ("Project", "Scene", "Frames", "Preset", "Status")


def frames_text(frame_range: FrameRange) -> str:
    mode = frame_range.mode
    if mode is FrameRangeMode.MANUAL:
        text = f"{frame_range.start}..{frame_range.end}"
        return text if frame_range.step == 1 else f"{text} step {frame_range.step}"
    if mode is FrameRangeMode.SINGLE:
        return str(frame_range.frame)
    if mode is FrameRangeMode.LIST:
        return frame_range.frames_text or "list"
    return "from file" if frame_range.step == 1 else f"from file, step {frame_range.step}"


class QueueView(QGroupBox):
    add_requested = Signal()
    run_requested = Signal()
    remove_requested = Signal(list)
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Queue", parent)
        self._ids: list[str] = []

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents)

        self.add_button = QPushButton("Add current", self)
        self.add_button.setToolTip("Add the current project, frames and preset as a queue item")
        self.add_button.clicked.connect(self.add_requested)
        self.run_button = QPushButton("Run queue", self)
        self.run_button.clicked.connect(self.run_requested)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._emit_remove)
        self.clear_button = QPushButton("Clear finished", self)
        self.clear_button.clicked.connect(self.clear_requested)
        self.status_label = QLabel("", self)
        set_role(self.status_label, "muted")

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.status_label, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)

    # --- публичное API ---------------------------------------------------------

    def set_items(self, items: list[QueueItem]) -> None:
        self._ids = [item.id for item in items]
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            status = item.status
            if item.frames_total and status in ("running", "paused", "failed", "stopped"):
                status = f"{status} {item.frames_done}/{item.frames_total}"
            values = (item.title, item.job.scene or "—", frames_text(item.job.frame_range), item.job.preset or "—", status)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(item.message or item.job.blend_path)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, cell)
        pending = sum(1 for item in items if item.status == "pending")
        self.status_label.setText(f"{len(items)} item(s), {pending} pending" if items else "Queue is empty")
        self.run_button.setEnabled(pending > 0 and self.run_button.text() == "Run queue")

    def set_running(self, running: bool) -> None:
        self.run_button.setText("Queue running…" if running else "Run queue")
        self.run_button.setEnabled(not running and self.table.rowCount() > 0)
        self.add_button.setEnabled(True)

    def selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._ids[row] for row in rows if row < len(self._ids)]

    def _emit_remove(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.remove_requested.emit(ids)
