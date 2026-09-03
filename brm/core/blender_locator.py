"""Поиск и проверка blender.exe без запуска Blender.

Проверка версии через пробу (probe_caps) — этап M1. Здесь только файловая
валидация: существует, это файл, это .exe, это именно blender.exe.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

BLENDER_EXE_NAME = "blender.exe"
LAUNCHER_EXE_NAME = "blender-launcher.exe"

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class BlenderPathStatus:
    """Результат проверки пути. ``reason`` — текст для баннера и тултипа."""

    ok: bool
    reason: str = ""
    path: str | None = None


def validate_blender_path(path: str | os.PathLike[str] | None) -> BlenderPathStatus:
    """Файловая проверка пути к blender.exe. Blender не запускается."""
    text = str(path).strip() if path is not None else ""
    if not text:
        return BlenderPathStatus(False, "Не выбран blender.exe")
    p = Path(text)
    if not p.exists():
        return BlenderPathStatus(False, f"Файл не найден: {p}", text)
    if p.is_dir():
        return BlenderPathStatus(False, f"Это папка, а не файл: {p}", text)
    name = p.name.lower()
    if name == LAUNCHER_EXE_NAME:
        return BlenderPathStatus(
            False, "Выбран blender-launcher.exe. Нужен blender.exe из той же папки", text
        )
    if p.suffix.lower() != ".exe":
        return BlenderPathStatus(False, f"Это не исполняемый файл (.exe): {p.name}", text)
    if name != BLENDER_EXE_NAME:
        return BlenderPathStatus(False, f"Ожидается blender.exe, выбран {p.name}", text)
    if not os.access(p, os.X_OK):
        return BlenderPathStatus(False, f"Нет прав на запуск: {p}", text)
    return BlenderPathStatus(True, "", text)


def default_search_roots() -> list[Path]:
    """Папки, внутри которых лежат папки установок Blender (уровнем выше exe)."""
    roots: list[Path] = []
    for env in ("ProgramW6432", "ProgramFiles"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value) / "Blender Foundation")
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value) / "Steam" / "steamapps" / "common")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _version_key(exe: Path) -> tuple[int, int]:
    """Версия из имени папки установки: «Blender 4.5» → (4, 5). Без версии — (0, 0)."""
    match = _VERSION_RE.search(exe.parent.name)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


def find_blender_candidates(roots: Iterable[Path] | None = None) -> list[str]:
    """Ищет blender.exe в стандартных местах установки. Новые версии первыми."""
    found: list[Path] = []
    for root in roots if roots is not None else default_search_roots():
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            exe = child / BLENDER_EXE_NAME
            if child.is_dir() and exe.is_file() and exe not in found:
                found.append(exe)
    found.sort(key=lambda exe: (_version_key(exe), str(exe).lower()), reverse=True)
    return [str(exe) for exe in found]
