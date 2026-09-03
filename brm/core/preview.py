"""Превью последнего отрендеренного кадра (раздел 9 спеки, открытый вопрос).

Чистая логика: какой файл вообще можно показать и где взять самый свежий кадр,
если рендер прошёл раньше и приложение только что открыли. Само отображение —
в ``ui/preview_window.py``. Без Qt.
"""
from __future__ import annotations

import os
from pathlib import Path

from brm.core.output_scan import parse_output_template

# Что умеет показать Qt из коробки. EXR сюда не входит намеренно: формат
# из пресета Final, но Qt его не читает — честнее объяснить, чем показать пустоту.
PREVIEWABLE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp", "tga", "jp2"})
# Форматы, которые BRM реально пишет, но показать не может — для внятного сообщения.
KNOWN_UNPREVIEWABLE = {
    "exr": "OpenEXR",
    "hdr": "Radiance HDR",
    "cin": "Cineon",
    "dpx": "DPX",
    "rgb": "IRIS",
}


def _extension(path: str | os.PathLike[str]) -> str:
    return Path(path).suffix.lower().lstrip(".")


def is_previewable(path: str | os.PathLike[str]) -> bool:
    return _extension(path) in PREVIEWABLE_EXTENSIONS


def describe_unpreviewable(path: str | os.PathLike[str]) -> str:
    """Почему этот кадр не показать — текстом для окна превью."""
    extension = _extension(path)
    known = KNOWN_UNPREVIEWABLE.get(extension)
    name = Path(path).name
    if known:
        return f"{name} is {known}: Qt cannot display it. The frame is rendered, open it in a viewer that reads {known}."
    return f"{name}: unsupported image format for preview."


def latest_rendered_frame(
    output_path: str | os.PathLike[str], *, previewable_only: bool = True
) -> Path | None:
    """Самый последний кадр рядом с шаблоном вывода; None, если ничего нет.

    Берётся кадр с наибольшим номером, а не самый свежий по времени: после
    resume дорендеренные кадры моложе, но показать логичнее последний по счёту.
    """
    template = parse_output_template(output_path)
    directory = template.directory
    if not directory.is_dir():
        return None
    best: tuple[int, Path] | None = None
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.is_file():
            continue
        if previewable_only and not is_previewable(entry):
            continue
        number = _frame_number(entry.name, template.prefix, template.suffix, template.digits)
        if number is None:
            continue
        if best is None or number > best[0]:
            best = (number, entry)
    return best[1] if best is not None else None


def _frame_number(name: str, prefix: str, suffix: str, digits: int) -> int | None:
    """Номер кадра из имени файла по шаблону вывода; None, если имя не подходит."""
    stem = Path(name).stem
    if not stem.startswith(prefix) or not stem.endswith(suffix):
        return None
    middle = stem[len(prefix) : len(stem) - len(suffix)] if suffix else stem[len(prefix) :]
    if len(middle) < digits or not middle.isdigit():
        return None
    return int(middle)
