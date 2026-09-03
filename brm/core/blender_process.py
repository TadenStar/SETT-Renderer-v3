"""Один запуск blender.exe: список аргументов, таймаут, отмена, utf-8. Без Qt.

Для коротких проб (секунды). Живой рендер с построчным чтением stdout будет
в ``runner.py`` (M2–M3), там нужен QProcess.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


@dataclass
class BlenderResult:
    argv: list[str]
    returncode: int | None
    stdout: str
    duration: float
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    def brm_lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line.startswith("[BRM]")]

    def tail(self, lines: int = 30) -> str:
        return "\n".join(self.stdout.splitlines()[-lines:])


def script_path(name: str) -> Path:
    """Путь к скрипту из ``brm/scripts``, который будет выполнен внутри Blender."""
    path = SCRIPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Blender script not found: {path}")
    return path


def describe_failure(what: str, result: BlenderResult) -> str:
    """Текст для пользователя: почему запуск не удался, с хвостом лога."""
    if result.cancelled:
        return f"{what} was cancelled"
    if result.timed_out:
        return f"{what} timed out after {result.duration:.0f} s"
    tail = result.tail()
    return f"{what} failed with exit code {result.returncode}" + (f"\n{tail}" if tail else "")


def run_blender(
    blender_path: str | os.PathLike[str],
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float = 120.0,
    cancel: threading.Event | None = None,
    poll_interval: float = 0.25,
) -> BlenderResult:
    """Запускает Blender списком аргументов и ждёт завершения.

    stdout и stderr сливаются в одну строку (utf-8, errors=replace: в путях
    бывает кириллица). По таймауту или по ``cancel`` процесс убивается.
    """
    argv = [str(blender_path), *(str(a) for a in args)]
    env = dict(os.environ)
    # Построчный вывод и предсказуемая кодировка: Blender и его Python пишут UTF-8.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )

    # Читаем stdout в отдельном потоке: иначе процесс встанет на полном pipe,
    # а мы не сможем ни отменить его, ни отследить таймаут.
    chunks: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)

    reader = threading.Thread(target=pump, name="blender-stdout", daemon=True)
    reader.start()

    timed_out = cancelled = False
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        if time.monotonic() - started > timeout:
            timed_out = True
            break
        time.sleep(poll_interval)

    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    reader.join(timeout=5)

    return BlenderResult(
        argv=argv,
        returncode=proc.returncode,
        stdout="".join(chunks),
        duration=time.monotonic() - started,
        timed_out=timed_out,
        cancelled=cancelled,
    )
