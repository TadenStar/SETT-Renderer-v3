"""Фоновые задачи для UI: пробы Blender не должны замораживать окно.

Только plumbing: функция из core выполняется в пуле потоков Qt, результат или
текст ошибки приходит сигналом в главный поток.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class FunctionTask(QRunnable):
    """Выполняет ``fn(*args, cancel=Event, **kwargs)`` в пуле потоков.

    ``cancel`` передаётся функции, чтобы она могла убить дочерний процесс.
    Вызывающий код обязан держать ссылку на задачу до прихода сигнала.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = TaskSignals()
        self.cancel = threading.Event()
        self.tag: Any = None
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        # Объект живёт, пока на него ссылается вызывающий код, а не пока идёт run():
        # так к task.tag и task.cancel можно обращаться после завершения.
        self.setAutoDelete(False)

    def run(self) -> None:  # noqa: D401 — имя из Qt
        try:
            result = self._fn(*self._args, cancel=self.cancel, **self._kwargs)
        except Exception as exc:  # текст уходит в UI, стек — в лог
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.finished.emit(result)

    def start(self, pool: QThreadPool | None = None) -> FunctionTask:
        (pool or QThreadPool.globalInstance()).start(self)
        return self
