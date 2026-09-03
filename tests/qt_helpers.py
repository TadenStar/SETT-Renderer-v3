"""Помощники для Qt-тестов: ожидание состояния через цикл событий."""
from __future__ import annotations

import time
from collections.abc import Callable


def wait_until(qapp, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Крутит цикл событий, пока условие не выполнится: сигналы из потоков и процессов идут через очередь."""
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        time.sleep(0.01)
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for the UI state")
