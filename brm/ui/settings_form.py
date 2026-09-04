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
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from brm.core.capabilities import (
    COMPUTE_AUTO,
    COMPUTE_CPU,
    COMPUTE_GPU,
    COMPUTE_GPU_CPU,
    Capabilities,
)
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
# Чем считать: подписи для списка. Значения — как в AppSettings.compute_mode.
_COMPUTE_MODES = [
    ("Auto", COMPUTE_AUTO),
    ("GPU", COMPUTE_GPU),
    ("GPU + CPU", COMPUTE_GPU_CPU),
    ("CPU", COMPUTE_CPU),
]
# В списке два режима; Expert включается кнопкой «All settings…» и живёт
# в своём окне, поэтому страницы в стеке для него нет.
_VIEWS = [("Preset", VIEW_PRESET_ONLY), ("Simple", VIEW_SIMPLE)]
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
    ParamSpec("tiles", "Tile rendering", "cycles.use_auto_tile", None, "bool"),
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
    tuning_toggled = Signal(bool)
    save_preset_requested = Signal()
    delete_preset_requested = Signal()
    expert_requested = Signal()
    compute_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Render Settings", parent)
        self._presets: list[Preset] = []
        self._caps: Capabilities | None = None
        self._engine: str | None = None
        # Значения берутся из экспертного окна, а не из списка режимов.
        self._expert_mode = False

        self.preset_combo = QComboBox(self)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_index)
        self.save_preset_button = QPushButton("Save as…", self)
        self.save_preset_button.setToolTip("Save the current settings as your own preset")
        self.save_preset_button.clicked.connect(self.save_preset_requested)
        self.delete_preset_button = QPushButton("Delete", self)
        self.delete_preset_button.setToolTip("Delete the selected preset of your own")
        self.delete_preset_button.clicked.connect(self.delete_preset_requested)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_preset_button)
        preset_row.addWidget(self.delete_preset_button)

        self.description_label = QLabel("", self)
        self.description_label.setWordWrap(True)
        set_role(self.description_label, "muted")

        self.warning_label = QLabel("", self)
        self.warning_label.setWordWrap(True)
        set_role(self.warning_label, "warning")
        self.warning_label.hide()

        self.tune_check = QCheckBox("Tune for this machine", self)
        self.tune_check.setToolTip(
            "Trim the preset to the VRAM and RAM of this computer. Only memory layout is changed — "
            "the rendered image stays the same."
        )
        self.tune_check.toggled.connect(self.tuning_toggled)
        self.tuning_label = QLabel("", self)
        self.tuning_label.setWordWrap(True)
        set_role(self.tuning_label, "muted")
        self.tuning_label.hide()

        self.view_combo = QComboBox(self)
        for title, value in _VIEWS:
            self.view_combo.addItem(title, value)
        self.view_combo.setCurrentIndex(_VIEW_INDEX[VIEW_SIMPLE])
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        self.device_combo = QComboBox(self)
        for title, value in _COMPUTE_MODES:
            self.device_combo.addItem(title, value)
        self.device_combo.setToolTip(
            "GPU is the default. Adding the CPU helps on light scenes and gets in the way on heavy ones"
        )
        self.device_combo.currentIndexChanged.connect(
            lambda _index: self.compute_changed.emit(self.compute_mode())
        )
        self.cull_check = QCheckBox("Camera culling", self)
        self.cull_check.setToolTip(
            "Skip objects outside the camera view. Cycles only; the .blend file is not changed"
        )
        self.cull_check.toggled.connect(self.values_changed)
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Device:", self))
        device_row.addWidget(self.device_combo)
        device_row.addWidget(self.cull_check)
        device_row.addStretch(1)

        self.expert_button = QPushButton("All settings…", self)
        self.expert_button.setToolTip("Every property this Blender exposes, in a separate window")
        self.expert_button.clicked.connect(self.expert_requested)

        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Mode:", self))
        view_row.addWidget(self.view_combo)
        view_row.addStretch(1)
        view_row.addWidget(self.expert_button)

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

        # Экспертная форма живёт здесь, а показывается в отдельном окне: сотни
        # свойств на главном экране пугали больше, чем помогали.
        self.expert_form = ExpertForm()
        self.expert_form.values_changed.connect(self.values_changed)
        # Простые строки прокручиваются: панель может оказаться низкой,
        # и тогда поля не должны сминаться до нечитаемого состояния.
        self._simple_scroll = QScrollArea(self)
        self._simple_scroll.setWidget(simple_page)
        self._simple_scroll.setWidgetResizable(True)
        self._simple_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(preset_only_page)
        self.stack.addWidget(self._simple_scroll)
        self.stack.setCurrentIndex(_VIEW_INDEX[VIEW_SIMPLE])

        layout = QVBoxLayout(self)
        layout.addLayout(preset_row)
        layout.addWidget(self.description_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.tune_check)
        layout.addWidget(self.tuning_label)
        layout.addLayout(device_row)
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

    def compute_mode(self) -> str:
        return self.device_combo.currentData() or COMPUTE_AUTO

    def set_compute_mode(self, mode: str) -> None:
        index = self.device_combo.findData(mode)
        self.device_combo.blockSignals(True)
        self.device_combo.setCurrentIndex(index if index >= 0 else 0)
        self.device_combo.blockSignals(False)

    def camera_culling(self) -> bool:
        return self.cull_check.isChecked()

    def set_camera_culling(self, enabled: bool) -> None:
        self.cull_check.blockSignals(True)
        self.cull_check.setChecked(enabled)
        self.cull_check.blockSignals(False)

    def tuning_enabled(self) -> bool:
        return self.tune_check.isChecked()

    def set_tuning_enabled(self, enabled: bool) -> None:
        self.tune_check.blockSignals(True)
        self.tune_check.setChecked(enabled)
        self.tune_check.blockSignals(False)

    def show_tuning(self, hardware_summary: str, notes: list[str] | None) -> None:
        """Строка под чекбоксом: что за машина и что именно урезано.

        ``notes is None`` — подстройка выключена или железо неизвестно.
        """
        if not self.tune_check.isChecked():
            self.tuning_label.hide()
            return
        if notes is None:
            self.tuning_label.setText(f"{hardware_summary} — nothing to trim automatically")
            set_role(self.tuning_label, "muted")
        elif notes:
            self.tuning_label.setText(f"{hardware_summary} — {', '.join(notes)}")
            set_role(self.tuning_label, "ok")
        else:
            self.tuning_label.setText(f"{hardware_summary} — preset already fits")
            set_role(self.tuning_label, "muted")
        self.tuning_label.show()

    def display_mode(self) -> str:
        return VIEW_EXPERT if self._expert_mode else self.view_combo.currentData()

    def set_display_mode(self, mode: str) -> None:
        """Expert не выбирается списком: он включается кнопкой и живёт в своём окне."""
        if mode == VIEW_EXPERT:
            self._expert_mode = True
            self._sync_mode_note()
            self.values_changed.emit()
            return
        self._expert_mode = False
        index = self.view_combo.findData(mode)
        if index >= 0:
            self.view_combo.setCurrentIndex(index)
        self._sync_mode_note()

    def _sync_mode_note(self) -> None:
        """Пока значения берутся из экспертного окна, список режимов не врёт."""
        self.view_combo.setEnabled(not self._expert_mode)
        self.expert_button.setText("All settings…" if not self._expert_mode else "All settings (in use)…")

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
        # По имени режима из списка, а не по индексу комбобокса: порядок страниц
        # стека и пунктов списка не обязан совпадать вечно. display_mode() здесь
        # не годится — он может вернуть Expert, у которого страницы нет.
        self.stack.setCurrentIndex(_VIEW_INDEX[self.view_combo.currentData()])
        self.values_changed.emit()  # смена режима меняет действующие overrides

    def _show_description(self) -> None:
        preset = self.current_preset()
        self.description_label.setText(preset.description if preset else "")
        warning = preset.warning if preset else ""
        self.warning_label.setText(warning)
        self.warning_label.setVisible(bool(warning))
        # Встроенные пресеты удалять нельзя: они лежат в дистрибутиве.
        self.delete_preset_button.setEnabled(preset is not None and not preset.builtin)

    def current_preset(self) -> Preset | None:
        name = self.current_preset_name()
        return next((p for p in self._presets if p.name == name), None)
