"""Что за машина под нами: VRAM, RAM, потоки CPU.

Нужно, чтобы подстроить пресет под конкретное железо (``hardware_tuning``).
Ни один источник не обязателен: не нашли ``nvidia-smi``, стоит карта AMD,
запустились не под Windows — соответствующее поле остаётся ``None``, в
``notes`` ложится причина, и подстройка просто не трогает то, о чём не знает.
Никаких исключений наружу. Без Qt.

VRAM берётся у ``nvidia-smi``, а не у Blender: список устройств Cycles
(``caps.cycles.devices``) даёт имя и тип карты, но не объём памяти.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
NVIDIA_SMI = "nvidia-smi"
NVIDIA_SMI_ARGS = ("--query-gpu=name,memory.total", "--format=csv,noheader,nounits")
# Проба должна быть быстрой: она держит первый показ настроек.
PROBE_TIMEOUT_S = 10


@dataclass(frozen=True)
class HardwareInfo:
    """Итог пробы. Пустой объект — «ничего не известно», это рабочее состояние."""

    gpu_name: str = ""
    vram_mb: int | None = None
    ram_mb: int | None = None
    cpu_threads: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def vram_gb(self) -> float | None:
        return None if self.vram_mb is None else self.vram_mb / 1024

    @property
    def ram_gb(self) -> float | None:
        return None if self.ram_mb is None else self.ram_mb / 1024

    def is_known(self) -> bool:
        """Есть ли хоть что-то, на что можно опереться при подстройке."""
        return self.vram_mb is not None or self.ram_mb is not None

    def summary(self) -> str:
        """Строка для статус-бара и лога, на английском, как весь интерфейс."""
        parts: list[str] = []
        if self.gpu_name:
            gpu = self.gpu_name
            if self.vram_mb is not None:
                gpu += f" ({round_gb(self.vram_mb)} GB VRAM)"
            parts.append(gpu)
        elif self.vram_mb is not None:
            parts.append(f"{round_gb(self.vram_mb)} GB VRAM")
        if self.ram_mb is not None:
            parts.append(f"{round_gb(self.ram_mb)} GB RAM")
        if self.cpu_threads:
            parts.append(f"{self.cpu_threads} threads")
        return " · ".join(parts) if parts else "Hardware unknown"

    def matches_render_device(self, device_names: list[str]) -> bool | None:
        """Та ли это карта, на которой будет считать Cycles.

        ``None`` — сравнивать не с чем. Сравнение по нормализованному имени:
        Blender и драйвер расходятся в словах «NVIDIA»/«GeForce» и пробелах.
        """
        if not self.gpu_name or not device_names:
            return None
        mine = _normalize_gpu(self.gpu_name)
        return any(_normalize_gpu(name) == mine for name in device_names)


def round_gb(mb: int) -> int:
    """Гигабайты для человека: 8151 MiB — это «8 GB», а не «7.96»."""
    return max(1, round(mb / 1024))


def _normalize_gpu(name: str) -> str:
    return " ".join(name.lower().replace("nvidia", "").replace("geforce", "").split())


def parse_nvidia_smi(output: str) -> tuple[str, int | None]:
    """Первая строка ``name, memory.total`` → имя и мегабайты.

    Мусор, пустой вывод или «[N/A]» вместо числа — ``("", None)``. Несколько
    карт: берём первую, это встроенный сценарий одной машины из спеки.
    """
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, memory = line.partition(",")
        name = name.strip()
        digits = "".join(ch for ch in memory if ch.isdigit())
        if not name:
            continue
        return name, int(digits) if digits else None
    return "", None


def nvidia_smi_path() -> str | None:
    """``nvidia-smi`` из PATH, иначе штатное место в System32."""
    from shutil import which

    found = which(NVIDIA_SMI)
    if found:
        return found
    system_root = os.environ.get("SystemRoot")
    if system_root:
        fallback = Path(system_root) / "System32" / f"{NVIDIA_SMI}.exe"
        if fallback.is_file():
            return str(fallback)
    return None


def detect_gpu() -> tuple[str, int | None, str]:
    """(имя, VRAM в МБ, причина неудачи). Причина пустая, когда всё получилось."""
    executable = nvidia_smi_path()
    if executable is None:
        return "", None, "nvidia-smi not found: GPU memory unknown (non-NVIDIA card or no driver tools)"
    try:
        result = subprocess.run(
            [executable, *NVIDIA_SMI_ARGS],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("nvidia-smi failed: %s", exc)
        return "", None, f"Could not run nvidia-smi: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return "", None, detail[0] if detail else f"nvidia-smi exited with code {result.returncode}"
    name, vram_mb = parse_nvidia_smi(result.stdout or "")
    if vram_mb is None:
        return name, None, "nvidia-smi reported no memory size"
    return name, vram_mb, ""


def detect_ram_mb() -> tuple[int | None, str]:
    """Физическая память через GlobalMemoryStatusEx. Не Windows — молча None."""
    if os.name != "nt":
        return None, "System memory size is only detected on Windows"
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:  # pragma: no cover — не воспроизводится на Windows
        log.warning("GlobalMemoryStatusEx failed: %s", exc)
        return None, f"Could not read system memory: {exc}"
    if not ok or status.ullTotalPhys == 0:
        return None, "Could not read system memory size"
    return int(status.ullTotalPhys // (1024 * 1024)), ""


def detect_hardware(*, cancel: Event | None = None) -> HardwareInfo:
    """Полная проба. Медленная часть — ``nvidia-smi``, поэтому вызывать из потока."""
    notes: list[str] = []
    gpu_name, vram_mb, gpu_note = "", None, ""
    if cancel is None or not cancel.is_set():
        gpu_name, vram_mb, gpu_note = detect_gpu()
    if gpu_note:
        notes.append(gpu_note)
    ram_mb, ram_note = detect_ram_mb()
    if ram_note:
        notes.append(ram_note)
    return HardwareInfo(
        gpu_name=gpu_name,
        vram_mb=vram_mb,
        ram_mb=ram_mb,
        cpu_threads=os.cpu_count() or 0,
        notes=notes,
    )
