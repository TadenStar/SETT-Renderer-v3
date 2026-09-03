"""Общие фикстуры. Qt работает в offscreen-режиме, окна не показываются."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


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


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
