"""Общие фикстуры. Qt работает в offscreen-режиме, окна не показываются.

Интеграционные тесты с маркером ``blender`` запускают настоящий blender.exe:
путь берётся из переменной ``BRM_BLENDER`` или автопоиском. Без Blender они
пропускаются, а не падают.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Тесты никогда не должны писать в настоящие %APPDATA%/%LOCALAPPDATA% пользователя.

    settings.json, queue.json, history.db, кэш capabilities и временные файлы —
    всё производится от этих двух переменных (core/storage.py). Без изоляции
    тест, который не передал MainWindow явный store (queue_store/history_store),
    молча писал бы в реальный профиль — так однажды и утекло в history.db
    пользователя, обнаружено при разборе M7.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))


# Железо тестовой машины: фиксированное, чтобы результат не зависел от того,
# на чём гоняют тесты. 8 ГБ VRAM — целевой ноутбук из спеки.
TEST_HARDWARE_VRAM_MB = 8151
TEST_HARDWARE_RAM_MB = 32189


@pytest.fixture(autouse=True)
def _stub_hardware_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """MainWindow не должен запускать настоящий nvidia-smi в каждом тесте.

    Без подмены проба стартует на каждом окне: это внешний процесс, разное
    железо у разных машин и разный результат подстройки пресета. Тесты,
    которым важно конкретное железо, передают свой ``hardware_detector=``.
    """
    from brm.core.hardware import HardwareInfo
    from brm.ui import main_window as main_window_mod

    def stub(*args, **kwargs) -> HardwareInfo:
        return HardwareInfo(
            gpu_name="NVIDIA GeForce RTX 5070 Laptop GPU",
            vram_mb=TEST_HARDWARE_VRAM_MB,
            ram_mb=TEST_HARDWARE_RAM_MB,
            cpu_threads=24,
        )

    monkeypatch.setattr(main_window_mod, "detect_hardware", stub)


@pytest.fixture(autouse=True)
def _close_leftover_windows():
    """Закрывает окна, созданные тестом, и отдаёт Qt их удалить.

    Тесты создают MainWindow и диалоги десятками и никогда их не закрывают.
    Виджеты копились на весь прогон вместе со своими QProcess и таймерами,
    и в случайном порядке набор начал ронять тест сборки видео: в
    фиксированном порядке он проходил, в случайном — нет. Причина не в
    нагрузке машины, а в отсутствии изоляции.
    """
    yield
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    app.processEvents()


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


@pytest.fixture
def fake_blender(tmp_path: Path) -> Path:
    """Поддельный blender.exe: файловой валидации достаточно имени и расширения."""
    exe = tmp_path / "Blender 5.0" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ fake")
    return exe


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def _find_real_blender() -> str | None:
    override = os.environ.get("BRM_BLENDER")
    if override and Path(override).is_file():
        return override
    from brm.core.blender_locator import find_blender_candidates

    candidates = find_blender_candidates()
    return candidates[0] if candidates else None


@pytest.fixture(scope="session")
def real_blender() -> str:
    path = _find_real_blender()
    if not path:
        pytest.skip("Real Blender not found; set BRM_BLENDER to run integration tests")
    return path


@pytest.fixture(scope="session")
def real_ffmpeg() -> str:
    from brm.core.ffmpeg import find_ffmpeg

    override = os.environ.get("BRM_FFMPEG")
    path = override if override and Path(override).is_file() else find_ffmpeg()
    if not path:
        pytest.skip("ffmpeg not found; set BRM_FFMPEG to run video integration tests")
    return path


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
