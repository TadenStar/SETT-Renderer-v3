"""Диапазон кадров (раздел 4.2 спеки): из файла, вручную, один кадр, список.

Список в синтаксисе Blender: ``1,5,10..20`` без пробелов уходит в
``--render-frame`` как есть. Пробелы и форма ``10-20`` допускаются во вводе
и нормализуются.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

_SINGLE_RE = re.compile(r"^-?\d+$")
_DOTS_RE = re.compile(r"^(-?\d+)\.\.(-?\d+)$")
_DASH_RE = re.compile(r"^(\d+)-(\d+)$")


class FrameRangeMode(str, Enum):
    FROM_FILE = "from_file"
    MANUAL = "manual"
    SINGLE = "single"
    LIST = "list"


class FrameRange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: FrameRangeMode = FrameRangeMode.FROM_FILE
    start: int = 1
    end: int = 1
    step: int = Field(default=1, ge=1)
    frame: int = 1
    frames_text: str = ""


def parse_frame_list(text: str) -> list[int]:
    """``"1, 5, 10..20, 30-32"`` → отсортированный список без дублей."""
    frames: set[int] = set()
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        if _SINGLE_RE.match(token):
            frames.add(int(token))
            continue
        match = _DOTS_RE.match(token) or _DASH_RE.match(token)
        if match is None:
            raise ValueError(f"Cannot parse frame list near '{token}'")
        first, last = int(match.group(1)), int(match.group(2))
        if last < first:
            raise ValueError(f"Range '{token}' ends before it starts")
        frames.update(range(first, last + 1))
    if not frames:
        raise ValueError("Frame list is empty")
    return sorted(frames)


def format_frame_list(frames: list[int]) -> str:
    """Обратно в синтаксис Blender: подряд идущие кадры сворачиваются в ``a..b``."""
    if not frames:
        return ""
    ordered = sorted(set(frames))
    parts: list[str] = []
    run_start = prev = ordered[0]
    for frame in ordered[1:] + [None]:  # type: ignore[list-item]
        if frame is not None and frame == prev + 1:
            prev = frame
            continue
        if run_start == prev:
            parts.append(str(run_start))
        elif prev == run_start + 1:
            parts.extend((str(run_start), str(prev)))
        else:
            parts.append(f"{run_start}..{prev}")
        if frame is not None:
            run_start = prev = frame
    return ",".join(parts)


def resolve_frames(
    frame_range: FrameRange,
    *,
    scene_start: int | None = None,
    scene_end: int | None = None,
) -> list[int]:
    """Итоговый список кадров для этой задачи."""
    mode = frame_range.mode
    if mode is FrameRangeMode.FROM_FILE:
        if scene_start is None or scene_end is None:
            raise ValueError("Frame range 'from file' needs scene information")
        start, end = scene_start, scene_end
    elif mode is FrameRangeMode.MANUAL:
        start, end = frame_range.start, frame_range.end
    elif mode is FrameRangeMode.SINGLE:
        return [frame_range.frame]
    else:
        return parse_frame_list(frame_range.frames_text)
    if end < start:
        raise ValueError(f"Frame range ends before it starts ({start}..{end})")
    return list(range(start, end + 1, frame_range.step))


def describe_frames(frames: list[int]) -> str:
    """Короткая подпись для UI: ``250 frames (1..250)``."""
    if not frames:
        return "no frames"
    if len(frames) == 1:
        return f"1 frame ({frames[0]})"
    return f"{len(frames)} frames ({frames[0]}..{frames[-1]})"
