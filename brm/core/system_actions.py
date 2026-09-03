"""Уведомления и автовыключение ПК после очереди (раздел 4.9 и 4.1 спеки).

Выключение делается штатным ``shutdown``: задержка даёт окно на отмену,
``cancel_shutdown`` отзывает запрос. Функции возвращают результат, а не
кидаются исключениями: не смогли выключить — приложение просто скажет об этом.
Без Qt.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Окно на отмену, как в спеке: 60 секунд.
SHUTDOWN_DELAY_S = 60
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def _run(argv: list[str]) -> ActionResult:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ActionResult(False, f"Could not run {argv[0]}: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return ActionResult(False, detail[0] if detail else f"{argv[0]} exited with code {result.returncode}")
    return ActionResult(True, "")


def is_supported() -> bool:
    return os.name == "nt"


def schedule_shutdown(delay_s: int = SHUTDOWN_DELAY_S, comment: str = "BRM: render queue finished") -> ActionResult:
    """Запрашивает выключение через ``delay_s`` секунд. Отменяется ``cancel_shutdown``."""
    if not is_supported():
        return ActionResult(False, "Shutdown is only supported on Windows")
    delay = max(int(delay_s), 0)
    result = _run(["shutdown", "/s", "/t", str(delay), "/c", comment[:511]])
    if result.ok:
        log.info("Shutdown scheduled in %s s", delay)
        return ActionResult(True, f"Shutdown scheduled in {delay} s")
    return result


def cancel_shutdown() -> ActionResult:
    """Отзывает запланированное выключение. Если его не было, Windows вернёт ошибку — это не беда."""
    if not is_supported():
        return ActionResult(False, "Shutdown is only supported on Windows")
    result = _run(["shutdown", "/a"])
    if result.ok:
        log.info("Shutdown cancelled")
        return ActionResult(True, "Shutdown cancelled")
    return ActionResult(False, result.message)
