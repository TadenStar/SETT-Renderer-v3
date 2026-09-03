"""Хранение настроек приложения.

Файл: ``%APPDATA%/BRM/settings.json``. Читается при старте, пишется при
изменении из диалога настроек. Модуль не зависит от Qt.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)

APP_DIR_NAME = "BRM"
SETTINGS_FILE_NAME = "settings.json"


class AppSettings(BaseModel):
    """Глобальные настройки приложения (раздел 4.1 спеки).

    Неизвестные ключи игнорируются, чтобы старая версия приложения
    могла прочитать файл, записанный более новой.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    # Путь к blender.exe. None — не задан. Протухший путь тоже хранится,
    # чтобы пользователь видел, что именно протухло.
    blender_path: str | None = None
    # Путь к ffmpeg.exe. Пусто — сборка видео отключена.
    ffmpeg_path: str | None = None
    default_output_dir: str | None = None
    shutdown_after_queue: bool = False
    theme: Literal["system", "light", "dark"] = "system"


def app_data_dir() -> Path:
    """Папка данных приложения: ``%APPDATA%/BRM``."""
    base = os.environ.get("APPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_DIR_NAME


def default_settings_path() -> Path:
    return app_data_dir() / SETTINGS_FILE_NAME


class SettingsStore:
    """Чтение и запись settings.json.

    Путь можно подменить (в тестах — на временный файл).
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_settings_path()

    def load(self) -> AppSettings:
        """Возвращает настройки из файла или дефолты.

        Битый файл не роняет приложение: он переименовывается в
        ``settings.json.broken-<дата>``, возвращаются дефолты.
        """
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON root is not an object")
            return AppSettings.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("Failed to read %s: %s", self.path, exc)
            self._quarantine()
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        """Атомарная запись: сначала во временный файл, потом ``os.replace``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        text = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2)
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def _quarantine(self) -> None:
        """Убирает битый файл с дороги, не удаляя его."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.broken-{stamp}")
        try:
            os.replace(self.path, target)
            log.warning("Corrupt settings file kept as %s", target)
        except OSError as exc:
            log.warning("Could not rename corrupt settings file: %s", exc)
