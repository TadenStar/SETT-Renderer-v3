"""Тесты core/app_paths.py: резолв обычного и «собранного» PyInstaller-режима."""
from __future__ import annotations

import sys

from brm.core.app_paths import is_frozen, package_root


def test_dev_mode_finds_the_real_repo_root() -> None:
    assert not is_frozen()
    root = package_root()
    assert (root / "brm" / "scripts" / "probe_caps.py").is_file()
    assert (root / "brm" / "resources" / "presets" / "super.json").is_file()
    assert (root / "brm.spec").is_file()


def test_frozen_mode_uses_meipass(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert is_frozen()
    assert package_root() == tmp_path


def test_frozen_flag_without_meipass_is_not_frozen(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert not is_frozen()
