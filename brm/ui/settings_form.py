"""Панель «Render Settings» (раздел 4.3 спеки): пресет и три режима отображения.

- **Preset** — доверяем пресету целиком, полей не показываем.
- **Simple** — 6–8 главных параметров (исходная форма M4).
- **Expert** — вся форма из capabilities с поиском (``ui/expert_form.py``, M7).

У каждого параметра три состояния: «Preset» (значение из пресета, только
показ), «Custom» (своё значение), «Don't touch» (не трогать то, что в файле).
Только отображение: резолв пресета делает core.preset_resolver, панель
показывает результат и отдаёт свои значения — из активного режима отображения.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from brm.core.capabilities import Capabilities
from brm.core.preset_resolver import FILE_FORMAT_PATH, ResolvedPreset
from brm.core.presets import Preset
from brm.ui.expert_form import ExpertForm
from brm.ui.field_modes import MODE_CUSTOM, MODE_PRESET, MODE_SKIP
from brm.ui.theme import set_role

_MODES = [("Preset", MODE_PRESET), ("Custom", MODE_CUSTOM), ("Don't touch", MODE_SKIP)]
DEFAULT_FORMATS = ["PNG", "JPEG", "OPEN_EXR", "OPEN_EXR_MULTILAYER", "TIFF", "BMP"]

VIEW_PRESET_ONLY = "preset_only"
VIEW_SIMPLE = "simple"
VIEW_EXPERT = "expert"
_VIEWS = [("Preset", VIEW_PRESET_ONLY), ("Simple", VIEW_SIMPLE), ("Expert", VIEW_EXPERT)]
_VIEW_INDEX = {value: index for index, (_title, value) in enumerate(_VIEWS)}


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    cycles_path: str | None
    eevee_path: str | None
    kind: str
    minimum: float = 0
    maximum: float = 1
    decimals: int = 0
    step: float = 1
    choices: list[str] = field(default_factory=list)

    def path_for(self, engine: str | None) -> str | None:
        if engine is None:
            return self.cycles_path or self.eevee_path
        if engine == "CYCLES":
            return self.cycles_path
        if engine.startswith("BLENDER_EEVEE"):
            return self.eevee_path
        return None


PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec("samples", "Max samples", "cycles.samples", "eevee.taa_render_samples", "int", 1, 65536),
    ParamSpec("threshold", "Noise threshold", "cycles.adaptive_threshold", None, "float", 0.0, 1.0, decimals=3, step=0.005),
    ParamSpec("bounces", "Max bounces", "cycles.max_bounces", None, "int", 0, 64),
    ParamSpec("denoise", "Denoise", "cycles.use_denoising", None, "bool"),
    ParamSpec("time_limit", "Time limit per frame, s", "cycles.time_limit", None, "int", 0, 86400),
    ParamSpec("resolution", "Resolution %", "render.resolution_percentage", "render.resolution_percentage", "int", 1, 400),
    ParamSpec("persistent", "Persistent Data", "render.use_persistent_data", "render.use_persistent_data", "bool"),
    ParamSpec("format", "File format", FILE_FORMAT_PATH, FILE_FORMAT_PATH, "enum", choices=DEFAULT_FORMATS),
)


class ParamRow(QWidget):
    changed = Signal()

    def __init__(self, spec: ParamSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.engine: str | None = None
        self.mode_combo = QComboBox(self)
        for title, value in _MODES:
            self.mode_combo.addItem(title, value)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.widget: QWidget
        if spec.kind == "int":
            spin = QSpinBox(self)
            spin.setRange(int(spec.minimum), int(spec.maximum))
            spin.valueChanged.connect(self.changed)
            self.widget = spin
        elif spec.kind == "float":
            dspin = QDoubleSpinBox(self)
            dspin.setRange(spec.minimum, spec.maximum)
            dspin.setDecimals(spec.decimals)
            dspin.setSingleStep(spec.step)
            dspin.valueChanged.connect(self.changed)
            self.widget = dspin
        elif spec.kind == "bool":
            check = QCheckBox(self)
            check.toggled.connect(self.changed)
            self.widget = check
        else:
            combo = QComboBox(self)
            combo.addItems(spec.choices)
            combo.currentIndexChanged.connect(self.changed)
            self.widget = combo

        self.note = QLabel(self)
        set_role(self.note, "muted")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode_combo)
        layout.addWidget(self.widget, 1)
        layout.addWidget(self.note)
        self._apply_mode()

    # --- публичное API ---------------------------------------------------------

    def mode(self) -> str:
        return self.mode_combo.currentData()

    def set_mode(self, mode: str) -> None:
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def path(self) -> str | None:
        return self.spec.path_for(self.engine)

    def set_engine(self, engine: str | None) -> None:
        self.engine = engine
        self._apply_mode()

    def set_choices(self, choices: list[str]) -> None:
        if not isinstance(self.widget, QComboBox):
            return
        current = self.widget.currentText()
        self.widget.blockSignals(True)
        self.widget.clear()
        self.widget.addItems(choices)
        if current in choices:
            self.widget.setCurrentText(current)
        self.widget.blockSignals(False)

    def show_preset_value(self, value: Any) -> None:
        """Значение из пресета: показываем, если режим Preset; None — пресет не задаёт."""
        self._preset_value = value
        if self.mode() == MODE_PRESET:
            self._set_widget_value(value)
        self._apply_mode()

    def value(self) -> Any:
        widget = self.widget
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        return widget.value()

    def set_value(self, value: Any) -> None:
        self._set_widget_value(value)

    # --- внутреннее ------------------------------------------------------------------

    _preset_value: Any = None

    def _set_widget_value(self, value: Any) -> None:
        widget = self.widget
        widget.blockSignals(True)
        try:
            if value is None:
                pass
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                if widget.findText(str(value)) < 0:
                    widget.addItem(str(value))
                widget.setCurrentText(str(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            else:
                widget.setValue(int(value))
        finally:
            widget.blockSignals(False)

    def _on_mode_changed(self, _index: int) -> None:
        if self.mode() == MODE_PRESET:
            self._set_widget_value(self._preset_value)
        self._apply_mode()
        self.changed.emit()

    def _apply_mode(self) -> None:
        path = self.path()
        if path is None:
            self.mode_combo.setEnabled(False)
            self.widget.setEnabled(False)
            self.note.setText("Cycles only")
            return
        self.mode_combo.setEnabled(True)
        mode = self.mode()
        self.widget.setEnabled(mode == MODE_CUSTOM)
        if mode == MODE_PRESET and self._preset_value is None:
            self.note.setText("not set by preset")
        elif mode == MODE_SKIP:
            self.note.setText("as in file")
        else:
            self.note.setText("")


class SettingsForm(QGroupBox):
    preset_changed = Signal(str)
    values_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Render Settings", parent)
        self._presets: list[Preset] = []
        self._caps: Capabilities | None = None
        self._engine: str | None = None

        self.preset_combo = QComboBox(self)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_index)
        self.description_label = QLabel("", self)
        self.description_label.setWordWrap(True)
        set_role(self.description_label, "muted")

        self.view_combo = QComboBox(self)
        for title, value in _VIEWS:
            self.view_combo.addItem(title, value)
        self.view_combo.setCurrentIndex(_VIEW_INDEX[VIEW_SIMPLE])
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Mode:", self))
        view_row.addWidget(self.view_combo)
        view_row.addStretch(1)

        self.skipped_label = QLabel("", self)
        self.skipped_label.setWordWrap(True)
        set_role(self.skipped_label, "warning")
        self.skipped_label.hide()

        preset_only_page = QLabel(
            "Every setting comes from the preset above. Switch to Simple or Expert to change values.", self
        )
        preset_only_page.setWordWrap(True)
        set_role(preset_only_page, "muted")

        simple_page = QWidget(self)
        self.rows: dict[str, ParamRow] = {}
        simple_form = QFormLayout(simple_page)
        simple_form.setContentsMargins(0, 0, 0, 0)
        for spec in PARAMS:
            row = ParamRow(spec, simple_page)
            row.changed.connect(self.values_changed)
            self.rows[spec.key] = row
            simple_form.addRow(f"{spec.label}:", row)

        self.expert_form = ExpertForm(self)
        self.expert_form.values_changed.connect(self.values_changed)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(preset_only_page)
        self.stack.addWidget(simple_page)
        self.stack.addWidget(self.expert_form)
        self.stack.setCurrentIndex(_VIEW_INDEX[VIEW_SIMPLE])

        layout = QVBoxLayout(self)
        layout.addWidget(self.preset_combo)
        layout.addWidget(self.description_label)
        layout.addLayout(view_row)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.skipped_label)

    # --- публичное API ---------------------------------------------------------

    def set_presets(self, presets: list[Preset], current: str | None = None) -> None:
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

    def display_mode(self) -> str:
        return self.view_combo.currentData()

    def set_display_mode(self, mode: str) -> None:
        index = self.view_combo.findData(mode)
        if index >= 0:
            self.view_combo.setCurrentIndex(index)

    def set_capabilities(self, caps: Capabilities | None) -> None:
        self._caps = caps
        self.expert_form.set_capabilities(caps)

    def set_engine(self, engine: str | None) -> None:
        self._engine = engine
        for row in self.rows.values():
            row.set_engine(engine)
        self.expert_form.set_engine(engine)

    def set_format_choices(self, choices: list[str]) -> None:
        self.rows["format"].set_choices(choices)

    def show_resolved(self, resolved: ResolvedPreset | None) -> None:
        """Значения пресета в строках; пропущенные настройки — одной строкой с тултипом."""
        for row in self.rows.values():
            path = row.path()
            row.show_preset_value(resolved.value(path) if (resolved and path) else None)
        self.expert_form.show_resolved(resolved)
        if resolved is None or not resolved.skipped:
            self.skipped_label.hide()
            return
        count = len(resolved.skipped)
        self.skipped_label.setText(f"⚠ {count} preset setting(s) are not available in this Blender, see the log")
        self.skipped_label.setToolTip("\n".join(f"{s.path}: {s.reason}" for s in resolved.skipped))
        self.skipped_label.show()

    def custom_values(self) -> dict[str, Any]:
        mode = self.display_mode()
        if mode == VIEW_EXPERT:
            return self.expert_form.custom_values()
        if mode == VIEW_PRESET_ONLY:
            return {}
        return {row.path(): row.value() for row in self.rows.values() if row.path() and row.mode() == MODE_CUSTOM}

    def untouched_paths(self) -> set[str]:
        mode = self.display_mode()
        if mode == VIEW_EXPERT:
            return self.expert_form.untouched_paths()
        if mode == VIEW_PRESET_ONLY:
            return set()
        return {row.path() for row in self.rows.values() if row.path() and row.mode() == MODE_SKIP}  # type: ignore[misc]

    # --- слоты -------------------------------------------------------------------

    def _on_preset_index(self, _index: int) -> None:
        self._show_description()
        name = self.current_preset_name()
        if name:
            self.preset_changed.emit(name)

    def _on_view_changed(self, _index: int) -> None:
        # По имени режима, а не по индексу комбобокса: порядок страниц стека
        # и пунктов списка не обязан совпадать вечно.
        self.stack.setCurrentIndex(_VIEW_INDEX[self.display_mode()])
        self.values_changed.emit()  # смена режима меняет действующие overrides

    def _show_description(self) -> None:
        name = self.current_preset_name()
        preset = next((p for p in self._presets if p.name == name), None)
        self.description_label.setText(preset.description if preset else "")
