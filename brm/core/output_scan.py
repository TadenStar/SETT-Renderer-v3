"""Скан папки вывода (раздел 4.6 спеки): какие кадры есть, какие битые, какие рендерить.

Шаблон вывода Blender: последняя группа ``#`` в имени задаёт число цифр
(``####`` → ``0001``); без ``#`` Blender дописывает четыре цифры к имени.
Расширение зависит от формата, поэтому ищем файлы кадра с любым расширением,
а при известном формате — только с его расширением. Без Qt.
"""
from __future__ import annotations

import glob
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_HASHES_RE = re.compile(r"#+")

EXTENSIONS = {
    "PNG": "png",
    "JPEG": "jpg",
    "JPEG2000": "jp2",
    "BMP": "bmp",
    "TIFF": "tif",
    "TARGA": "tga",
    "TARGA_RAW": "tga",
    "IRIS": "rgb",
    "HDR": "hdr",
    "WEBP": "webp",
    "OPEN_EXR": "exr",
    "OPEN_EXR_MULTILAYER": "exr",
    "CINEON": "cin",
    "DPX": "dpx",
}


def extension_for_format(file_format: str | None) -> str | None:
    """Расширение файла кадра для формата Blender; None для неизвестного."""
    if not file_format:
        return None
    return EXTENSIONS.get(file_format.upper())


@dataclass(frozen=True)
class OutputTemplate:
    directory: Path
    prefix: str
    suffix: str
    digits: int

    def file_stem(self, frame: int) -> str:
        return f"{self.prefix}{frame:0{self.digits}d}{self.suffix}"


def parse_output_template(output_path: str | os.PathLike[str]) -> OutputTemplate:
    path = Path(output_path)
    name = path.name
    matches = list(_HASHES_RE.finditer(name))
    if matches:
        last = matches[-1]
        return OutputTemplate(path.parent, name[: last.start()], name[last.end() :], len(last.group()))
    return OutputTemplate(path.parent, name, "", 4)


@dataclass
class FrameFile:
    frame: int
    path: Path | None = None
    size: int = 0

    @property
    def exists(self) -> bool:
        return self.path is not None


@dataclass
class OutputScan:
    template: OutputTemplate
    files: dict[int, FrameFile] = field(default_factory=dict)

    def existing(self, min_size_bytes: int = 0) -> list[int]:
        return [f for f, info in self.files.items() if info.exists and info.size >= max(min_size_bytes, 1)]

    def missing(self, frames: Iterable[int], min_size_bytes: int = 0) -> list[int]:
        """Кадры, которых нет или которые меньше порога (битые, пустые)."""
        good = set(self.existing(min_size_bytes))
        return [f for f in frames if f not in good]

    def undersized(self, min_size_bytes: int) -> list[int]:
        return [f for f, info in self.files.items() if info.exists and info.size < min_size_bytes]


def scan_output(
    output_path: str | os.PathLike[str],
    frames: Iterable[int],
    *,
    extensions: Iterable[str] | None = None,
) -> OutputScan:
    """Ищет файл каждого кадра. Из нескольких (jpg от Draft и exr от Final) берёт самый большой."""
    template = parse_output_template(output_path)
    scan = OutputScan(template)
    allowed = {e.lower().lstrip(".") for e in extensions} if extensions else None
    directory = template.directory
    for frame in frames:
        info = FrameFile(frame=frame)
        if directory.is_dir():
            pattern = os.path.join(glob.escape(str(directory)), glob.escape(template.file_stem(frame)) + ".*")
            best: tuple[int, Path] | None = None
            for candidate in glob.glob(pattern):
                path = Path(candidate)
                if allowed is not None and path.suffix.lower().lstrip(".") not in allowed:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if best is None or size > best[0]:
                    best = (size, path)
            if best is not None:
                info.size, info.path = best
        scan.files[frame] = info
    return scan
