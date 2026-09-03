"""Диалог глобальных настроек приложения (раздел 4.1 спеки).

Только отображение и сигналы: путь проверяет ``core.blender_locator``,
сохраняет вызывающий код через ``core.storage``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
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
from brm.core.storage import AppSettings

_STYLE_OK = "color: #2E7D32;"
_STYLE_ERROR = "color: #C62828;"


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки BRM")
        self.setMinimumWidth(860)
        self._settings = settings

        self.blender_edit = QLineEdit(settings.blender_path or "", self)
        self.blender_edit.setMinimumWidth(320)
        self.blender_edit.setPlaceholderText(
            r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
        )
        self.blender_status = QLabel(self)
        self.blender_status.setWordWrap(True)
        browse_blender = QPushButton("Обзор…", self)
        browse_blender.clicked.connect(self._browse_blender)
        autodetect = QPushButton("Найти автоматически", self)
        autodetect.clicked.connect(self._autodetect_blender)

        self.ffmpeg_edit = QLineEdit(settings.ffmpeg_path or "", self)
        self.ffmpeg_edit.setMinimumWidth(320)
        self.ffmpeg_edit.setPlaceholderText("Пусто — сборка видео отключена")
        browse_ffmpeg = QPushButton("Обзор…", self)
        browse_ffmpeg.clicked.connect(self._browse_ffmpeg)

        self.output_edit = QLineEdit(settings.default_output_dir or "", self)
        self.output_edit.setMinimumWidth(320)
        self.output_edit.setPlaceholderText(r"D:\out")
        browse_output = QPushButton("Обзор…", self)
        browse_output.clicked.connect(self._browse_output)

        form = QFormLayout()
        form.addRow("Blender (blender.exe):", self._row(self.blender_edit, browse_blender, autodetect))
        form.addRow("", self.blender_status)
        form.addRow("ffmpeg (необязательно):", self._row(self.ffmpeg_edit, browse_ffmpeg))
        form.addRow("Папка вывода по умолчанию:", self._row(self.output_edit, browse_output))

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("OK")
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(self._buttons)

        self.blender_edit.textChanged.connect(self._revalidate)
        self._revalidate()

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
            }
        )

    # --- слоты ---------------------------------------------------------------

    def _revalidate(self) -> None:
        status = validate_blender_path(self.blender_edit.text())
        if status.ok:
            self.blender_status.setText("Файл найден. Версия проверится при первой пробе.")
            self.blender_status.setStyleSheet(_STYLE_OK)
        else:
            self.blender_status.setText(status.reason)
            self.blender_status.setStyleSheet(_STYLE_ERROR)
        self.ok_button.setEnabled(status.ok)

    def _browse_blender(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите blender.exe",
            _start_dir(self.blender_edit.text()),
            "blender.exe (blender.exe);;Исполняемые файлы (*.exe)",
        )
        if path:
            self.blender_edit.setText(_native(path))

    def _autodetect_blender(self) -> None:
        candidates = find_blender_candidates()
        if not candidates:
            self.blender_status.setText(
                "Не нашёл Blender в стандартных папках. Укажите путь через «Обзор…»."
            )
            self.blender_status.setStyleSheet(_STYLE_ERROR)
            return
        if len(candidates) == 1:
            self.blender_edit.setText(candidates[0])
            return
        choice, accepted = QInputDialog.getItem(
            self, "Найдены установки Blender", "Выберите сборку:", candidates, 0, False
        )
        if accepted and choice:
            self.blender_edit.setText(choice)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите ffmpeg.exe",
            _start_dir(self.ffmpeg_edit.text()),
            "ffmpeg.exe (ffmpeg.exe);;Исполняемые файлы (*.exe)",
        )
        if path:
            self.ffmpeg_edit.setText(_native(path))

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Папка вывода по умолчанию", _start_dir(self.output_edit.text())
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
