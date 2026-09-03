"""Панель «History» (раздел 4.9 спеки): таблица прошедших рендеров с сортировкой
и график времени кадров выбранной записи.

Только отображение: main_window читает time кадров через
``core.history.read_frame_times`` и передаёт готовыми в ``show_chart``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from brm.core.history import HistoryEntry
from brm.core.render_stats import format_duration, format_memory
from brm.ui.sparkline import Sparkline
from brm.ui.theme import set_role

COLUMNS = ("Date", "Project", "Scene", "Preset", "Engine", "Frames", "Avg frame", "Total", "Peak mem", "Status")


def _short_date(iso: str) -> str:
    return iso.replace("T", " ")[:16] if iso else "—"


class _NumericItem(QTableWidgetItem):
    """Сортировка по числу, а не по отформатированному тексту ("1m 05s" < "42 s")."""

    def __init__(self, text: str, sort_value: float) -> None:
        super().__init__(text)
        self._sort_value = sort_value

    def __lt__(self, other: object) -> bool:
        if isinstance(other, _NumericItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


class HistoryView(QWidget):
    row_selected = Signal(str)  # stats_path выбранной записи; "" — ничего не выбрано
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[HistoryEntry] = []

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.count_label = QLabel("", self)
        set_role(self.count_label, "muted")

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.chart_label = QLabel("Select a render to see its frame times", self)
        set_role(self.chart_label, "muted")
        self.sparkline = Sparkline(self)

        header_row = QHBoxLayout()
        header_row.addWidget(self.refresh_button)
        header_row.addWidget(self.count_label, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.chart_label)
        layout.addWidget(self.sparkline)

    # --- публичное API ---------------------------------------------------------

    def set_entries(self, entries: list[HistoryEntry]) -> None:
        self._entries = list(entries)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            items = [
                QTableWidgetItem(_short_date(entry.finished_at)),
                QTableWidgetItem(entry.project),
                QTableWidgetItem(entry.scene or "—"),
                QTableWidgetItem(entry.preset or "—"),
                QTableWidgetItem(entry.engine or "—"),
                _NumericItem(f"{entry.frames_done} / {entry.frames_total}", entry.frames_done),
                _NumericItem(format_duration(entry.avg_frame_time_s), entry.avg_frame_time_s or -1.0),
                _NumericItem(format_duration(entry.duration_s), entry.duration_s),
                _NumericItem(format_memory(entry.peak_mem_mb), entry.peak_mem_mb or -1.0),
                QTableWidgetItem(entry.status),
            ]
            items[0].setData(Qt.ItemDataRole.UserRole, entry.stats_path)
            for column, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"{len(entries)} render(s)")
        self.show_chart([], "")

    def selected_entry(self) -> HistoryEntry | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return None
        stats_path = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((e for e in self._entries if e.stats_path == stats_path), None)

    def show_chart(self, times: list[tuple[int, float]], label: str) -> None:
        self.sparkline.set_values(times)
        self.chart_label.setText(label or "Select a render to see its frame times")

    # --- слоты -------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        entry = self.selected_entry()
        self.row_selected.emit(entry.stats_path if entry else "")
