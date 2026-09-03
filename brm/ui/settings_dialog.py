"""Диалог глобальных настроек приложения (раздел 4.1 спеки).

Только отображение и сигналы: путь проверяет ``core.blender_locator``,
сохраняет вызывающий код через ``core.storage``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from brm.core.blender_locator import find_blender_candidates, validate_blender_path
from brm.core.ffmpeg import find_ffmpeg, validate_ffmpeg_path
from brm.core.storage import AppSettings
from brm.ui.theme import set_role

_THEME_CHOICES = [("Dark", "dark"), ("Light", "light"), ("System", "system")]
FFMPEG_DOWNLOAD_HINT = "Not installed? Get a build from ffmpeg.org or run: winget install Gyan.FFmpeg"


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BRM Settings")
        self.setMinimumWidth(860)
        self._settings = settings

        self.blender_edit = QLineEdit(settings.blender_path or "", self)
        self.blender_edit.setMinimumWidth(320)
        self.blender_edit.setPlaceholderText(
            r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
        )
        self.blender_status = QLabel(self)
        self.blender_status.setWordWrap(True)
        browse_blender = QPushButton("Browse…", self)
        browse_blender.clicked.connect(self._browse_blender)
        autodetect = QPushButton("Auto-detect", self)
        autodetect.clicked.connect(self._autodetect_blender)

        self.ffmpeg_edit = QLineEdit(settings.ffmpeg_path or "", self)
        self.ffmpeg_edit.setMinimumWidth(320)
        self.ffmpeg_edit.setPlaceholderText("Empty — video assembly disabled")
        self.ffmpeg_status = QLabel(self)
        self.ffmpeg_status.setWordWrap(True)
        browse_ffmpeg = QPushButton("Browse…", self)
        browse_ffmpeg.clicked.connect(self._browse_ffmpeg)
        autodetect_ffmpeg = QPushButton("Auto-detect", self)
        autodetect_ffmpeg.clicked.connect(self._autodetect_ffmpeg)

        self.notifications_check = QCheckBox("Notify when a job or the queue finishes", self)
        self.notifications_check.setChecked(settings.notifications)
        self.shutdown_check = QCheckBox("Shut down the PC after the queue finishes", self)
        self.shutdown_check.setChecked(settings.shutdown_after_queue)
        self.shutdown_check.setToolTip("A 60-second window with a Cancel button appears before the shutdown")

        self.output_edit = QLineEdit(settings.default_output_dir or "", self)
        self.output_edit.setMinimumWidth(320)
        self.output_edit.setPlaceholderText(r"D:\out")
        browse_output = QPushButton("Browse…", self)
        browse_output.clicked.connect(self._browse_output)

        self.theme_combo = QComboBox(self)
        for title, value in _THEME_CHOICES:
            self.theme_combo.addItem(title, value)
        index = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(index if index >= 0 else 0)

        form = QFormLayout()
        form.addRow("Blender (blender.exe):", self._row(self.blender_edit, browse_blender, autodetect))
        form.addRow("", self.blender_status)
        form.addRow("ffmpeg (optional):", self._row(self.ffmpeg_edit, browse_ffmpeg, autodetect_ffmpeg))
        form.addRow("", self.ffmpeg_status)
        form.addRow("Default output folder:", self._row(self.output_edit, browse_output))
        form.addRow("Theme:", self.theme_combo)
        form.addRow("After render:", self.notifications_check)
        form.addRow("", self.shutdown_check)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("OK")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(self._buttons)

        self.blender_edit.textChanged.connect(self._revalidate)
        self.ffmpeg_edit.textChanged.connect(self._revalidate_ffmpeg)
        self._revalidate()
        self._revalidate_ffmpeg()

    # --- публичное API -------------------------------------------------------

    def focus_blender_path(self) -> None:
        self.blender_edit.setFocus()
        self.blender_edit.selectAll()

    def result_settings(self) -> AppSettings:
        """Настройки с учётом введённого. Пустые поля → None."""
        return self._settings.model_copy(
            update={
                "blender_path": _none_if_blank(self.blender_edit.text()),
                "ffmpeg_path": _none_if_blank(self.ffmpeg_edit.text()),
                "default_output_dir": _none_if_blank(self.output_edit.text()),
                "theme": self.theme_combo.currentData(),
                "notifications": self.notifications_check.isChecked(),
                "shutdown_after_queue": self.shutdown_check.isChecked(),
            }
        )

    # --- слоты ---------------------------------------------------------------

    def _revalidate(self) -> None:
        status = validate_blender_path(self.blender_edit.text())
        if status.ok:
            self.blender_status.setText("File found. Version will be checked on the first probe.")
            set_role(self.blender_status, "ok")
        else:
            self.blender_status.setText(status.reason)
            set_role(self.blender_status, "error")
        self.ok_button.setEnabled(status.ok)

    def _browse_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select blender.exe",
            _start_dir(self.blender_edit.text()),
            "blender.exe (blender.exe);;Executables (*.exe)",
        )
        if path:
            self.blender_edit.setText(_native(path))

    def _autodetect_blender(self) -> None:
        candidates = find_blender_candidates()
        if not candidates:
            self.blender_status.setText(
                "No Blender found in the standard folders. Use Browse… to pick blender.exe."
            )
            set_role(self.blender_status, "error")
            return
        if len(candidates) == 1:
            self.blender_edit.setText(candidates[0])
            return
        choice, accepted = QInputDialog.getItem(
            self, "Blender installations found", "Choose a build:", candidates, 0, False
        )
        if accepted and choice:
            self.blender_edit.setText(choice)

    def _revalidate_ffmpeg(self) -> None:
        text = self.ffmpeg_edit.text().strip()
        if not text:
            self.ffmpeg_status.setText(f"Video assembly is disabled. {FFMPEG_DOWNLOAD_HINT}")
            set_role(self.ffmpeg_status, "muted")
            return
        status = validate_ffmpeg_path(text)
        self.ffmpeg_status.setText("ffmpeg found." if status.ok else status.reason)
        set_role(self.ffmpeg_status, "ok" if status.ok else "error")

    def _autodetect_ffmpeg(self) -> None:
        found = find_ffmpeg()
        if found:
            self.ffmpeg_edit.setText(found)
            return
        self.ffmpeg_status.setText(f"No ffmpeg found in PATH or the usual folders. {FFMPEG_DOWNLOAD_HINT}")
        set_role(self.ffmpeg_status, "error")

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ffmpeg.exe",
            _start_dir(self.ffmpeg_edit.text()),
            "ffmpeg.exe (ffmpeg.exe);;Executables (*.exe)",
        )
        if path:
            self.ffmpeg_edit.setText(_native(path))

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Default output folder", _start_dir(self.output_edit.text())
        )
        if path:
            self.output_edit.setText(_native(path))

    # --- утилиты компоновки --------------------------------------------------

    def _row(self, edit: QLineEdit, *buttons: QPushButton) -> QWidget:
        box = QWidget(self)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        for button in buttons:
            layout.addWidget(button, 0)
        return box


def _none_if_blank(text: str) -> str | None:
    text = text.strip()
    return text or None


def _native(path: str) -> str:
    """Qt отдаёт пути с прямыми слешами, пользователю привычнее обратные."""
    return str(Path(path))


def _start_dir(current: str) -> str:
    current = current.strip()
    if not current:
        return ""
    p = Path(current)
    return str(p if p.is_dir() else p.parent)
