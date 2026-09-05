"""Разбор вывода Blender (раздел 6 спеки) по реальным логам 5.0.1 из ``tests/fixtures/logs``.

Blender 5.x печатает в формате CLOG: ``00:01.140  render | Fra: 1 | Mem: 360M | Sample 1/16``.
Старый формат без префикса (``Fra:12 Mem:1024.55M (Peak 1200.00M) | ... | Sample 128/512``)
разбирается теми же выражениями. Нет совпадения — событие ``other``; парсер не падает.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BRM_PREFIX = "[BRM]"

# Префикс clog ищется в любом месте строки, а не только в начале: Blender пишет
# статистику Cycles другим потоком и врезает её в середину строки, из-за чего
# «Saved:» теряло начало строки и ни один кадр не засчитывался (найдено на
# рендере из 1441 кадра: 98 строк Saved, ни одной с начала строки).
RE_CLOG = re.compile(r"(?P<ts>\d{2}:\d{2}(?::\d{2})?\.\d{3})\s+(?P<cat>[\w.]+)\s+\|\s?(?P<body>.*)$")
RE_FRA = re.compile(r"^Fra:\s*(?P<frame>-?\d+)")
RE_SAMPLE = re.compile(r"\bSample\s+(?P<cur>\d+)\s*/\s*(?P<total>\d+)")
RE_EEVEE_SAMPLE = re.compile(r"\bRendering\s+(?P<cur>\d+)\s*/\s*(?P<total>\d+)\s+samples")
RE_MEM = re.compile(r"\bMem:\s*(?P<val>[\d.]+)\s*(?P<unit>[KMG])")
RE_PEAK = re.compile(r"\bPeak(?:\s*Mem)?[: ]+\s*(?P<val>[\d.]+)\s*(?P<unit>[KMG])")
RE_REMAINING = re.compile(r"\bRemaining:\s*(?P<t>[\d:.]+)")
RE_SAVED = re.compile(r"^Saved:\s*'(?P<path>.+)'\s*$")
RE_TIME = re.compile(r"^Time:\s*(?P<t>[\d:.]+)(?:\s*\(Saving:\s*(?P<saving>[\d:.]+)\))?")
RE_FRAME_START = re.compile(r"^Rendering frame\s+(?P<frame>-?\d+)")
RE_ANIMATION = re.compile(r"^Rendering animation \(frames\s+(?P<first>-?\d+)\.\.(?P<last>-?\d+)\)")
RE_ENGINE = re.compile(r"^Engine:\s*(?P<name>.+?)\s*$")
RE_QUIT = re.compile(r"^Blender quit")
# Blender 5.x пишет отчёты как «reports | ERROR Cannot render, no camera».
RE_ERROR = re.compile(
    r"(Error:|\| ERROR |Traceback \(most recent call last\)|CUDA error|OptiX error|out of memory|"
    r"Cannot read file|\[BRM\] FAIL)"
)
RE_OOM = re.compile(
    r"out of (?:\w+\s+)?memory|CUDA_ERROR_OUT_OF_MEMORY|OPTIX_ERROR_OUT_OF_MEMORY|failed to allocate",
    re.IGNORECASE,
)

KIND_BRM = "brm"
KIND_ERROR = "error"
KIND_PROGRESS = "progress"
KIND_SAVED = "saved"
KIND_TIME = "time"
KIND_FRAME_START = "frame_start"
KIND_ANIMATION = "animation"
KIND_ENGINE = "engine"
KIND_QUIT = "quit"
KIND_OTHER = "other"


@dataclass
class LogEvent:
    kind: str
    raw: str
    body: str = ""
    frame: int | None = None
    sample: int | None = None
    samples_total: int | None = None
    mem_mb: float | None = None
    peak_mb: float | None = None
    remaining_s: float | None = None
    saved_path: str | None = None
    frame_time_s: float | None = None
    saving_time_s: float | None = None
    first_frame: int | None = None
    last_frame: int | None = None
    engine: str | None = None
    finished: bool = False


def parse_time(text: str) -> float | None:
    """``00:12.34`` или ``01:02:03.45`` → секунды. None, если не число."""
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def parse_mem(value: str, unit: str) -> float | None:
    """``360``, ``M`` → мегабайты."""
    try:
        number = float(value)
    except ValueError:
        return None
    factor = {"K": 1 / 1024, "M": 1.0, "G": 1024.0}.get(unit.upper())
    return None if factor is None else number * factor


def strip_clog_prefix(line: str) -> str:
    """Тело строки после префикса clog. Префикс может оказаться не в начале.

    ``search``, а не ``match``: посторонний вывод склеивается с началом строки
    лога, и тогда всё, что до префикса, — чужой мусор, который надо отбросить.
    """
    match = RE_CLOG.search(line)
    return match.group("body") if match else line


def is_brm_line(line: str) -> bool:
    return line.startswith(BRM_PREFIX)


def is_error_line(line: str) -> bool:
    return RE_ERROR.search(line) is not None


def is_out_of_memory(line: str) -> bool:
    return RE_OOM.search(line) is not None


def parse_line(line: str) -> LogEvent:
    """Одна строка вывода → событие. Мягкая деградация: неизвестное → ``other``."""
    raw = line.rstrip("\r\n")
    body = strip_clog_prefix(raw).strip()
    event = LogEvent(kind=KIND_OTHER, raw=raw, body=body)

    if is_brm_line(raw) and not raw.startswith("[BRM] FAIL"):
        event.kind = KIND_BRM
        return event
    if is_error_line(raw):
        event.kind = KIND_ERROR
        return event

    fra = RE_FRA.match(body)
    if fra:
        event.kind = KIND_PROGRESS
        event.frame = int(fra.group("frame"))
        sample = RE_SAMPLE.search(body) or RE_EEVEE_SAMPLE.search(body)
        if sample:
            event.sample = int(sample.group("cur"))
            event.samples_total = int(sample.group("total"))
        mem = RE_MEM.search(body)
        if mem:
            event.mem_mb = parse_mem(mem.group("val"), mem.group("unit"))
        peak = RE_PEAK.search(body)
        if peak:
            event.peak_mb = parse_mem(peak.group("val"), peak.group("unit"))
        remaining = RE_REMAINING.search(body)
        if remaining:
            event.remaining_s = parse_time(remaining.group("t"))
        event.finished = body.rstrip().endswith("Finished")
        return event

    saved = RE_SAVED.match(body)
    if saved:
        event.kind = KIND_SAVED
        event.saved_path = saved.group("path")
        return event

    timed = RE_TIME.match(body)
    if timed:
        event.kind = KIND_TIME
        event.frame_time_s = parse_time(timed.group("t"))
        if timed.group("saving"):
            event.saving_time_s = parse_time(timed.group("saving"))
        return event

    start = RE_FRAME_START.match(body)
    if start:
        event.kind = KIND_FRAME_START
        event.frame = int(start.group("frame"))
        return event

    animation = RE_ANIMATION.match(body)
    if animation:
        event.kind = KIND_ANIMATION
        event.first_frame = int(animation.group("first"))
        event.last_frame = int(animation.group("last"))
        return event

    engine = RE_ENGINE.match(body)
    if engine:
        event.kind = KIND_ENGINE
        event.engine = engine.group("name")
        return event

    if RE_QUIT.match(body):
        event.kind = KIND_QUIT
    return event
