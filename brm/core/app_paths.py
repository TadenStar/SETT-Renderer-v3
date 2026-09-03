"""Базовый путь пакетных ресурсов: ``brm/scripts`` и ``brm/resources`` (раздел 8 спеки, M8).

При обычном запуске это корень репозитория. В собранном PyInstaller
``--onefile`` экзешнике чистый Python-код архивируется внутрь exe и
``__file__`` не указывает на реальный файл на диске — данные (``datas`` в
``brm.spec``) распаковываются в ``sys._MEIPASS``, туда и нужно смотреть.
Без Qt.
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def package_root() -> Path:
    """Папка, где лежит пакет ``brm/`` (то есть ``brm/scripts``, ``brm/resources``)."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # brm/core/app_paths.py -> brm/core -> brm -> корень репозитория
    return Path(__file__).resolve().parent.parent.parent
