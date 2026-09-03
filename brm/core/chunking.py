"""Пачки кадров и политика ретраев (разделы 4.6, 4.7 и 6 спеки). Без Qt.

Chunking: длинная анимация бьётся на пачки, каждая — отдельный процесс Blender:
утечки памяти не копятся, падение убивает одну пачку. При out of memory пачка
перезапускается с урезанными настройками в порядке из раздела 9 документа
о настройках: texture limit → tile size → Persistent Data → денойз на CPU.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Шаги урезания при нехватке памяти, применяются накопительно.
OOM_RETRY_STEPS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("texture limit 2048", {"cycles.texture_limit_render": "2048"}),
    ("tile size 512", {"cycles.use_auto_tile": True, "cycles.tile_size": 512}),
    ("Persistent Data off", {"render.use_persistent_data": False}),
    ("denoising on CPU", {"cycles.denoising_use_gpu": False}),
)
MAX_OOM_RETRIES = len(OOM_RETRY_STEPS)
# Падение без out of memory (сон ноутбука, драйвер): один повтор с теми же настройками.
MAX_CRASH_RETRIES = 1


def split_chunks(frames: Sequence[int], chunk_size: int | None) -> list[list[int]]:
    """``[1..7]``, 3 → ``[[1,2,3],[4,5,6],[7]]``; без размера — одна пачка."""
    frames = list(frames)
    if not frames:
        return []
    if not chunk_size or chunk_size <= 0:
        return [frames]
    return [frames[i : i + chunk_size] for i in range(0, len(frames), chunk_size)]


def oom_retry_overrides(attempt: int) -> tuple[str, dict[str, Any]] | None:
    """Накопленные урезания для попытки ``attempt`` (с 1); None, когда шаги кончились."""
    if attempt < 1 or attempt > MAX_OOM_RETRIES:
        return None
    overrides: dict[str, Any] = {}
    notes = []
    for note, step in OOM_RETRY_STEPS[:attempt]:
        overrides.update(step)
        notes.append(note)
    return ", ".join(notes), overrides


def describe_chunk(frames: Sequence[int]) -> str:
    if not frames:
        return "no frames"
    if len(frames) == 1:
        return f"frame {frames[0]}"
    return f"frames {frames[0]}..{frames[-1]} ({len(frames)})"
