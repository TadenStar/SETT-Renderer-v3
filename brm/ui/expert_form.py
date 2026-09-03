"""Экспертный режим панели «Render Settings» (раздел 4.3 спеки).

Вся форма генерируется из capabilities: bool → чекбокс, enum → комбобокс,
int/float → спинбокс с границами из пробы (``soft_min``/``soft_max``, иначе
``hard_min``/``hard_max``). Поиск по имени свойства. Значение, отличающееся
от того, что даёт пресет, помечается текстом «≠ preset» — без новых цветов,
только роль ``muted``, как у остальных подсказок в панели.

Три состояния параметра (Preset/Custom/Don't touch) — общий протокол с
простой формой (``ui/field_modes.py``), поэтому главное окно собирает
overrides одинаково из любой активной формы.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from brm.core.capabilities import Capabilities, PropertyInfo
from brm.core.expert_fields import FieldSpec, list_fields
from brm.core.preset_resolver import ResolvedPreset
from brm.ui.field_modes import MODE_CUSTOM, MODE_PRESET, MODE_SKIP
from brm.ui.theme import set_role

_MODES = [("Preset", MODE_PRESET), ("Custom", MODE_CUSTOM), ("Don't touch", MODE_SKIP)]
_INT_MIN, _INT_MAX = -2_147_483_648, 2_147_483_647


def _int_range(info: PropertyInfo) -> tuple[int, int]:
    lo = info.soft_min if info.soft_min is not None else info.hard_min
    hi = info.soft_max if info.soft_max is not None else info.hard_max
    lo = _INT_MIN if lo is None else max(_INT_MIN, min(_INT_MAX, int(lo)))
    hi = _INT_MAX if hi is None else max(_INT_MIN, min(_INT_MAX, int(hi)))
    return (lo, hi) if hi > lo else (lo, lo + 1)


def _float_range(info: PropertyInfo) -> tuple[float, float]:
    lo = info.soft_min if info.soft_min is not None else info.hard_min
    hi = info.soft_max if info.soft_max is not None else info.hard_max
    lo = -1e6 if lo is None else float(lo)
    hi = 1e6 if hi is None else float(hi)
    return (lo, hi) if hi > lo else (lo, lo + 1.0)


class ExpertFieldRow(QWidget):
    """Одно свойство: комбобокс режима + виджет по типу + подсказка."""

    changed = Signal()

    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._preset_value: Any = None

        self.mode_combo = QComboBox(self)
        for title, value in _MODES:
            self.mode_combo.addItem(title, value)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.widget = self._build_widget(spec.info)
        self.note = QLabel(self)
        set_role(self.note, "muted")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode_combo)
        layout.addWidget(self.widget, 1)
        layout.addWidget(self.note)
        self._apply_mode()

    def _build_widget(self, info: PropertyInfo) -> QWidget:
        if info.type == "BOOLEAN":
            check = QCheckBox(self)
            check.toggled.connect(self._on_value_changed)
            return check
        if info.type == "ENUM":
            combo = QComboBox(self)
            combo.addItems(info.enum_identifiers())
            combo.currentIndexChanged.connect(self._on_value_changed)
            return combo
        if info.type == "FLOAT":
            dspin = QDoubleSpinBox(self)
            dspin.setRange(*_float_range(info))
            dspin.setDecimals(info.precision if info.precision is not None else 3)
            dspin.setSingleStep(0.01)
            dspin.valueChanged.connect(self._on_value_changed)
            return dspin
        spin = QSpinBox(self)
        spin.setRange(*_int_range(info))
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    # --- публичное API ---------------------------------------------------------

    def path(self) -> str:
        return self.spec.path

    def mode(self) -> str:
        return self.mode_combo.currentData()

    def set_mode(self, mode: str) -> None:
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def value(self) -> Any:
        widget = self.widget
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        return widget.value()

    def set_value(self, value: Any) -> None:
        self._set_widget_value(value)
        self._apply_mode()  # _set_widget_value блокирует сигналы — подсказку обновляем сами

    def show_preset_value(self, value: Any) -> None:
        """Значение из пресета: показываем, если режим Preset; None — пресет не задаёт."""
        self._preset_value = value
        if self.mode() == MODE_PRESET:
            self._set_widget_value(value)
        self._apply_mode()

    # --- внутреннее --------------------------------------------------------------

    def _set_widget_value(self, value: Any) -> None:
        widget = self.widget
        widget.blockSignals(True)
        try:
            if value is None:
                pass
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                text = str(value)
                if widget.findText(text) < 0:
                    widget.addItem(text)
                widget.setCurrentText(text)
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            else:
                widget.setValue(int(value))
        finally:
            widget.blockSignals(False)

    def _on_value_changed(self, *_args: Any) -> None:
        self._apply_mode()
        self.changed.emit()

    def _on_mode_changed(self, _index: int) -> None:
        if self.mode() == MODE_PRESET:
            self._set_widget_value(self._preset_value)
        self._apply_mode()
        self.changed.emit()

    def _apply_mode(self) -> None:
        mode = self.mode()
        self.widget.setEnabled(mode == MODE_CUSTOM)
        if mode == MODE_PRESET and self._preset_value is None:
            self.note.setText("not set by preset")
        elif mode == MODE_SKIP:
            self.note.setText("as in file")
        elif mode == MODE_CUSTOM and self._preset_value is not None and self.value() != self._preset_value:
            # Подсветка изменённого относительно пресета значения (раздел 4.3 спеки) —
            # текстом, без новой цветовой роли: в панели разрешены только muted/warning/error/ok.
            self.note.setText("≠ preset")
        else:
            self.note.setText("")


class ExpertForm(QWidget):
    """Вся форма: поиск + секции полей, сгенерированные из capabilities текущего движка."""

    values_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caps: Capabilities | None = None
        self._engine: str | None = None
        self._fields: list[FieldSpec] = []
        self.rows: dict[str, ExpertFieldRow] = {}
        self._row_containers: dict[str, QWidget] = {}
        self._section_headers: dict[str, QLabel] = {}

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search properties…")
        self.search_edit.textChanged.connect(self._apply_filter)
        self.count_label = QLabel(self)
        set_role(self.count_label, "muted")

        self.placeholder = QLabel(
            "Select a working Blender and load a project to see every engine property.", self
        )
        self.placeholder.setWordWrap(True)
        set_role(self.placeholder, "muted")

        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 4, 4, 4)
        self._content_layout.addStretch(1)  # секции вставляются перед этим — контент держится сверху

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._content)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        top = QHBoxLayout()
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.placeholder)
        layout.addWidget(self._scroll, 1)
        self._show_placeholder(True)

    # --- публичное API ---------------------------------------------------------

    def set_capabilities(self, caps: Capabilities | None) -> None:
        self._caps = caps
        self._rebuild_if_needed()

    def set_engine(self, engine: str | None) -> None:
        self._engine = engine
        self._rebuild_if_needed()

    def show_resolved(self, resolved: ResolvedPreset | None) -> None:
        for path, row in self.rows.items():
            row.show_preset_value(resolved.value(path) if resolved else None)

    def custom_values(self) -> dict[str, Any]:
        return {path: row.value() for path, row in self.rows.items() if row.mode() == MODE_CUSTOM}

    def untouched_paths(self) -> set[str]:
        return {path for path, row in self.rows.items() if row.mode() == MODE_SKIP}

    def field_count(self) -> int:
        return len(self._fields)

    # --- внутреннее --------------------------------------------------------------

    def _show_placeholder(self, show: bool) -> None:
        self.placeholder.setVisible(show)
        self._scroll.setVisible(not show)
        self.search_edit.setEnabled(not show)

    def _rebuild_if_needed(self) -> None:
        fields = list_fields(self._caps, self._engine)
        if [f.path for f in fields] == [f.path for f in self._fields]:
            return  # тот же движок и та же проба — состояние строк не теряем
        self._fields = fields
        self._rebuild(fields)

    def _rebuild(self, fields: list[FieldSpec]) -> None:
        while self._content_layout.count() > 1:  # последний элемент — финальный stretch, его не трогаем
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.rows.clear()
        self._row_containers.clear()
        self._section_headers.clear()

        if not fields:
            self._show_placeholder(True)
            self.count_label.setText("")
            return
        self._show_placeholder(False)

        current_section: str | None = None
        for spec in fields:
            if spec.section != current_section:
                current_section = spec.section
                header = QLabel(spec.section, self._content)
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                self._section_headers[spec.section] = header
                self._content_layout.insertWidget(self._content_layout.count() - 1, header)

            row = ExpertFieldRow(spec, self._content)
            row.changed.connect(self.values_changed)
            self.rows[spec.path] = row

            container = QWidget(self._content)
            row_layout = QHBoxLayout(container)
            row_layout.setContentsMargins(12, 0, 0, 0)
            label = QLabel(spec.label, container)
            label.setMinimumWidth(180)
            label.setToolTip(spec.info.description or spec.path)
            row_layout.addWidget(label)
            row_layout.addWidget(row, 1)
            self._row_containers[spec.path] = container
            self._content_layout.insertWidget(self._content_layout.count() - 1, container)

        self.count_label.setText(f"{len(fields)} properties")
        self._apply_filter(self.search_edit.text())

    def _apply_filter(self, query: str) -> None:
        query = query.strip()
        visible_sections: set[str] = set()
        for spec in self._fields:
            match = spec.matches(query)
            self._row_containers[spec.path].setVisible(match)
            if match:
                visible_sections.add(spec.section)
        for section, header in self._section_headers.items():
            header.setVisible(section in visible_sections)
        shown = sum(1 for spec in self._fields if spec.matches(query))
        self.count_label.setText(f"{shown} / {len(self._fields)}" if query else f"{len(self._fields)} properties")
