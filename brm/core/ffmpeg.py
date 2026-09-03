"""Сборка видео из секвенции внешним ffmpeg (раздел 4.8 спеки, раздел 6 настроек).

Железное правило: рендерим секвенцию кадров, видео собираем отдельным шагом.
Здесь только чистая логика — поиск бинарника, сборка argv, разбор прогресса.
Запуск процесса живёт в ``video_runner``. Без Qt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brm.core.app_paths import package_root
from brm.core.output_scan import parse_output_template
from brm.core.storage import app_data_dir

log = logging.getLogger(__name__)

BUILTIN_VIDEO_PRESETS_DIR = package_root() / "brm" / "resources" / "video"
FFMPEG_EXE_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
# ffmpeg пишет прогресс в stderr: «frame=  123 fps= 45 q=28.0 size=...».
RE_FFMPEG_FRAME = re.compile(r"\bframe=\s*(\d+)")
RE_FFMPEG_SPEED = re.compile(r"\bspeed=\s*([\d.]+)x")
RE_FFMPEG_TIME = re.compile(r"\btime=\s*(\d+:\d+:[\d.]+)")
# Расширения, которые ffmpeg читает как секвенцию изображений.
SEQUENCE_EXTENSIONS = ("png", "jpg", "jpeg", "exr", "tif", "tiff", "tga", "bmp", "webp", "dpx")


@dataclass(frozen=True)
class FfmpegPathStatus:
    """Результат проверки пути. ``reason`` — текст для UI."""

    ok: bool
    reason: str = ""
    path: str | None = None


def validate_ffmpeg_path(path: str | os.PathLike[str] | None) -> FfmpegPathStatus:
    """Файловая проверка пути к ffmpeg. Пустой путь — не ошибка: сборка просто отключена."""
    text = str(path).strip() if path is not None else ""
    if not text:
        return FfmpegPathStatus(False, "ffmpeg.exe is not set, video assembly is disabled")
    p = Path(text)
    if not p.exists():
        return FfmpegPathStatus(False, f"File not found: {p}", text)
    if p.is_dir():
        return FfmpegPathStatus(False, f"This is a folder, not a file: {p}", text)
    if p.stem.lower() != "ffmpeg":
        return FfmpegPathStatus(False, f"Expected ffmpeg, got {p.name}", text)
    if not os.access(p, os.X_OK):
        return FfmpegPathStatus(False, f"No permission to execute: {p}", text)
    return FfmpegPathStatus(True, "", text)


def find_ffmpeg() -> str | None:
    """ffmpeg в PATH или в типичных местах установки; None, если не найден."""
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found))
    candidates: list[Path] = []
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "ffmpeg" / "bin" / FFMPEG_EXE_NAME)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / FFMPEG_EXE_NAME)
    candidates.append(Path("C:/ffmpeg/bin") / FFMPEG_EXE_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class VideoPreset(BaseModel):
    """Пресет кодека. Аргументы ffmpeg перечислены явно: никакой магии со строками."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    name: str
    order: int = 100
    description: str = ""
    extension: str = "mp4"
    # Аргументы между входом и выходным файлом.
    args: list[str] = Field(default_factory=list)
    # Фильтр -vf; {width}/{height} подставляются, если заданы в пресете.
    video_filter: str | None = None
    width: int | None = None
    height: int | None = None
    faststart: bool = False
    builtin: bool = False
    source: str = ""

    def output_name(self, stem: str) -> str:
        return f"{stem}.{self.extension.lstrip('.')}"


class FfmpegError(RuntimeError):
    """Секвенцию не собрать: нет кадров, нет ffmpeg, неизвестный пресет."""


def user_video_presets_dir() -> Path:
    return app_data_dir() / "video"


def load_video_presets(
    builtin_dir: str | os.PathLike[str] | None = None,
    user_dir: str | os.PathLike[str] | None = None,
) -> list[VideoPreset]:
    """Встроенные плюс пользовательские, отсортированные по ``order``."""
    by_name: dict[str, VideoPreset] = {}
    sources: Iterable[tuple[Path, bool]] = (
        (Path(builtin_dir) if builtin_dir is not None else BUILTIN_VIDEO_PRESETS_DIR, True),
        (Path(user_dir) if user_dir is not None else user_video_presets_dir(), False),
    )
    for directory, builtin in sources:
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.json")):
            try:
                preset = VideoPreset.model_validate(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError) as exc:
                if builtin:
                    raise FfmpegError(f"Video preset {file.name} is unreadable: {exc}") from exc
                log.warning("Video preset %s is unreadable: %s", file.name, exc)
                continue
            preset.builtin = builtin
            preset.source = str(file)
            by_name[preset.name] = preset
    return sorted(by_name.values(), key=lambda p: (p.order, p.name.lower()))


def find_video_preset(presets: Iterable[VideoPreset], name: str | None) -> VideoPreset | None:
    return next((p for p in presets if p.name == name), None)


@dataclass
class SequenceInfo:
    """Секвенция кадров как вход для ffmpeg."""

    pattern: str  # D:/out/shot/%04d.png
    directory: Path
    first_frame: int
    frame_count: int
    extension: str
    stem: str  # имя для выходного файла

    @property
    def is_contiguous_from_first(self) -> bool:
        return self.frame_count > 0


def find_sequence(
    output_path: str | os.PathLike[str], extensions: Iterable[str] = SEQUENCE_EXTENSIONS
) -> SequenceInfo:
    """Ищет отрендеренные кадры рядом с шаблоном вывода и строит шаблон ``%04d`` для ffmpeg.

    Берётся самое многочисленное расширение: в папке могут лежать jpg от Draft
    и exr от Final. Кадры должны идти подряд, иначе ffmpeg остановится на дырке.
    """
    template = parse_output_template(output_path)
    directory = template.directory
    if not directory.is_dir():
        raise FfmpegError(f"Output folder does not exist: {directory}")

    name_re = re.compile(
        rf"^{re.escape(template.prefix)}(\d{{{template.digits}}}){re.escape(template.suffix)}\.(\w+)$"
    )
    by_extension: dict[str, list[int]] = {}
    allowed = {e.lower() for e in extensions}
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        match = name_re.match(entry.name)
        if match is None:
            continue
        extension = match.group(2).lower()
        if extension not in allowed:
            continue
        by_extension.setdefault(extension, []).append(int(match.group(1)))
    if not by_extension:
        raise FfmpegError(f"No rendered frames found in {directory}")

    extension = max(by_extension, key=lambda e: (len(by_extension[e]), e))
    frames = sorted(by_extension[extension])
    contiguous = 1
    while contiguous < len(frames) and frames[contiguous] == frames[contiguous - 1] + 1:
        contiguous += 1
    if contiguous < len(frames):
        log.warning("Frame sequence has a gap after %s, using %s frame(s)", frames[contiguous - 1], contiguous)

    pattern_name = f"{template.prefix}%0{template.digits}d{template.suffix}.{extension}"
    stem = (template.prefix + template.suffix).strip("_- .") or directory.name
    return SequenceInfo(
        pattern=str(directory / pattern_name),
        directory=directory,
        first_frame=frames[0],
        frame_count=contiguous,
        extension=extension,
        stem=stem,
    )


def build_ffmpeg_argv(
    ffmpeg_path: str | os.PathLike[str],
    sequence: SequenceInfo,
    preset: VideoPreset,
    output_file: str | os.PathLike[str],
    *,
    fps: float = 25.0,
    overwrite: bool = True,
) -> list[str]:
    """argv для сборки. Порядок важен: -framerate до -i, кодек после входа."""
    argv: list[str] = [str(ffmpeg_path), "-y" if overwrite else "-n", "-hide_banner"]
    argv += ["-framerate", _format_fps(fps)]
    if sequence.first_frame != 1:
        argv += ["-start_number", str(sequence.first_frame)]
    argv += ["-i", sequence.pattern]
    video_filter = _video_filter(preset)
    if video_filter:
        argv += ["-vf", video_filter]
    argv += list(preset.args)
    if preset.faststart:
        argv += ["-movflags", "+faststart"]
    argv.append(str(output_file))
    return argv


def _format_fps(fps: float) -> str:
    if fps <= 0:
        return "25"
    # 23.976 → 24000/1001: ffmpeg принимает дробь точнее числа с плавающей точкой.
    if abs(fps - round(fps)) < 0.001:
        return str(round(fps))
    return f"{fps:.3f}"


def _video_filter(preset: VideoPreset) -> str | None:
    if preset.video_filter:
        return preset.video_filter.format(width=preset.width or -2, height=preset.height or -2)
    if preset.width and preset.height:
        return f"scale={preset.width}:{preset.height}:flags=lanczos"
    return None


def preset_slug(name: str) -> str:
    """``H.264`` → ``h264``, ``Vertical 9:16`` → ``vertical_9_16``: имя, годное для файла."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name.lower().replace(".", ""))
    return slug.strip("_") or "video"


def default_output_file(sequence: SequenceInfo, preset: VideoPreset, folder: Path | None = None) -> Path:
    """Файл видео рядом с папкой кадров: ``…/cave_h264.mp4``."""
    base = folder or sequence.directory.parent
    stem = sequence.directory.name if sequence.stem in ("", sequence.directory.name) else sequence.stem
    return Path(base) / preset.output_name(f"{stem}_{preset_slug(preset.name)}")


@dataclass
class FfmpegProgress:
    """Состояние сборки: кадры, скорость, ошибки."""

    total_frames: int = 0
    frame: int = 0
    speed: float | None = None
    time: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        if not self.total_frames:
            return 0.0
        return min(max(self.frame / self.total_frames, 0.0), 1.0)


def parse_ffmpeg_line(line: str, progress: FfmpegProgress) -> bool:
    """Обновляет прогресс по строке ffmpeg. True, если строка что-то изменила."""
    changed = False
    frame = RE_FFMPEG_FRAME.search(line)
    if frame:
        progress.frame = int(frame.group(1))
        changed = True
    speed = RE_FFMPEG_SPEED.search(line)
    if speed:
        progress.speed = float(speed.group(1))
        changed = True
    stamp = RE_FFMPEG_TIME.search(line)
    if stamp:
        progress.time = stamp.group(1)
        changed = True
    lowered = line.lower()
    if ("error" in lowered or "invalid" in lowered or "no such file" in lowered) and line.strip():
        progress.errors.append(line.strip())
        changed = True
    return changed
