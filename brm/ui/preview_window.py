"""Окно превью последнего кадра (раздел 9 спеки).

Немодальное: его можно держать открытым во время рендера, в том числе на
втором мониторе. Кадр приходит из строки ``Saved:`` — трекер её уже разбирает.
Только отображение: какой файл показуем, решает ``core.preview``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from brm.ui.theme import set_role

EMPTY_TEXT = "No frame yet. Start a render, or open a project whose output folder already has frames."


class ImageView(QLabel):
    """Картинка, вписанная в доступное место с сохранением пропорций."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 180)
        self._source: QPixmap | None = None

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._source = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._rescale()

    def has_image(self) -> bool:
        return self._source is not None

    def _rescale(self) -> None:
        if self._source is None:
            self.clear()
            return
        self.setPixmap(
            self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — имя из Qt
        super().resizeEvent(event)
        self._rescale()


class PreviewWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Last Rendered Frame")
        self.resize(900, 620)
        self.current_path: Path | None = None

        self.image = ImageView(self)
        self.caption = QLabel(EMPTY_TEXT, self)
        self.caption.setWordWrap(True)
        set_role(self.caption, "muted")
        self.follow_check = QCheckBox("Follow the render", self)
        self.follow_check.setChecked(True)
        self.follow_check.setToolTip("Show every new frame as it is saved")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addWidget(self.follow_check)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.image, 1)
        layout.addWidget(self.caption)
        layout.addLayout(bottom)

    # --- публичное API ---------------------------------------------------------

    def follows_render(self) -> bool:
        return self.follow_check.isChecked()

    def show_frame(self, path: str | Path, note: str = "") -> bool:
        """Показывает кадр. False — файла нет или Qt его не прочитал."""
        target = Path(path)
        self.current_path = target
        if not target.is_file():
            self.image.set_pixmap(None)
            self._say(f"{target.name} is gone from disk.", "warning")
            return False
        pixmap = QPixmap(str(target))
        if pixmap.isNull():
            self.image.set_pixmap(None)
            self._say(note or f"{target.name}: could not read the image.", "warning")
            return False
        self.image.set_pixmap(pixmap)
        size = f"{pixmap.width()}×{pixmap.height()}"
        self._say(f"{target.name} · {size}" + (f" · {note}" if note else ""), "muted")
        return True

    def show_message(self, text: str, role: str = "muted") -> None:
        """Кадр показать нельзя (EXR, пустая папка) — объясняем словами."""
        self.image.set_pixmap(None)
        self._say(text, role)

    def clear(self) -> None:
        self.current_path = None
        self.image.set_pixmap(None)
        self._say(EMPTY_TEXT, "muted")

    # --- внутреннее ------------------------------------------------------------------

    def _say(self, text: str, role: str) -> None:
        self.caption.setText(text)
        set_role(self.caption, role)
