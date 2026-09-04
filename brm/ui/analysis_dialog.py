"""Окно анализа сцены: сколько объектов, геометрии и инстансов.

Открывается кнопкой в панели проекта. Число инстансов показывается отдельно от
числа объектов намеренно: тысяча инстансов одного меша и тысяча копий дают
одинаковое число объектов, но совершенно разную память.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from brm.core.scene_stats import SceneStats, format_count
from brm.ui.theme import set_role

HINT = (
    "Triangles are counted at render subdivision levels, so the number matches what Cycles builds, "
    "not what the viewport shows. The .blend file is never changed."
)


class AnalysisDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scene Analysis")
        self.resize(460, 340)

        self.status_label = QLabel("Analyzing…", self)
        self.status_label.setWordWrap(True)

        self.rows: dict[str, QLabel] = {}
        form_widget = QWidget(self)
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        for key, title in (
            ("objects", "Objects in scene:"),
            ("meshes", "Meshes:"),
            ("triangles", "Triangles (render):"),
            ("instances", "Instances:"),
            ("instanced_triangles", "Triangles from instances:"),
            ("camera_culled_objects", "Set to camera cull:"),
            ("objects_by_type", "By type:"),
        ):
            label = QLabel("—", form_widget)
            label.setWordWrap(True)
            self.rows[key] = label
            form.addRow(title, label)
        self.form_widget = form_widget
        self.form_widget.hide()

        hint = QLabel(HINT, self)
        hint.setWordWrap(True)
        set_role(hint, "muted")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.form_widget)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    # --- публичное API ---------------------------------------------------------

    def set_running(self, name: str) -> None:
        self.status_label.setText(f"Analyzing {name}… this reads the whole scene, heavy files take a while")
        set_role(self.status_label, "muted")
        self.form_widget.hide()

    def set_error(self, message: str) -> None:
        self.status_label.setText(message)
        set_role(self.status_label, "error")
        self.form_widget.hide()

    def set_stats(self, stats: SceneStats) -> None:
        self.status_label.setText(stats.summary())
        set_role(self.status_label, "")
        self.rows["objects"].setText(str(stats.objects))
        self.rows["meshes"].setText(str(stats.meshes))
        self.rows["triangles"].setText(f"{format_count(stats.triangles)} ({stats.triangles})")
        self.rows["instances"].setText(str(stats.instances))
        self.rows["instanced_triangles"].setText(
            f"{format_count(stats.instanced_triangles)} · {stats.instanced_share:.0%} of the geometry"
            if stats.instances
            else "—"
        )
        self.rows["camera_culled_objects"].setText(str(stats.camera_culled_objects))
        by_type = ", ".join(f"{name.lower()} {count}" for name, count in sorted(stats.objects_by_type.items()))
        self.rows["objects_by_type"].setText(by_type or "—")
        self.form_widget.show()
