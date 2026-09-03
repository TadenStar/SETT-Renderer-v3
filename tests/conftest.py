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
