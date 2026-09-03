"""Тесты core/blender_locator.py: файловая валидация и автопоиск."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from brm.core.blender_locator import (
    BlenderPathStatus,
    find_blender_candidates,
    validate_blender_path,
)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_path_is_not_ok(value) -> None:
    status = validate_blender_path(value)
    assert not status.ok
    assert "not set" in status.reason


def test_missing_file(tmp_path: Path) -> None:
    status = validate_blender_path(tmp_path / "nope" / "blender.exe")
    assert not status.ok
    assert "not found" in status.reason


def test_directory_instead_of_file(tmp_path: Path) -> None:
    status = validate_blender_path(tmp_path)
    assert not status.ok
    assert "folder" in status.reason


def test_launcher_is_rejected_with_hint(tmp_path: Path) -> None:
    exe = tmp_path / "blender-launcher.exe"
    exe.write_bytes(b"MZ")
    status = validate_blender_path(exe)
    assert not status.ok
    assert "blender-launcher.exe" in status.reason
    assert "blender.exe" in status.reason


def test_non_exe_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "blender.txt"
    file.write_text("x")
    status = validate_blender_path(file)
    assert not status.ok
    assert ".exe" in status.reason


def test_other_exe_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "notepad.exe"
    file.write_bytes(b"MZ")
    status = validate_blender_path(file)
    assert not status.ok
    assert "notepad.exe" in status.reason


def test_valid_blender_exe(fake_blender: Path) -> None:
    assert validate_blender_path(fake_blender) == BlenderPathStatus(True, "", str(fake_blender))


def test_name_check_is_case_insensitive(tmp_path: Path) -> None:
    exe = tmp_path / "BLENDER.EXE"
    exe.write_bytes(b"MZ")
    assert validate_blender_path(exe).ok


def test_surrounding_whitespace_is_stripped(fake_blender: Path) -> None:
    status = validate_blender_path(f"  {fake_blender}  ")
    assert status.ok
    assert status.path == str(fake_blender)


def test_no_exec_permission(fake_blender: Path, monkeypatch) -> None:
    monkeypatch.setattr(os, "access", lambda *args, **kwargs: False)
    status = validate_blender_path(fake_blender)
    assert not status.ok
    assert "permission" in status.reason


def test_find_candidates_sorted_newest_first(tmp_path: Path) -> None:
    root = tmp_path / "Blender Foundation"
    for name in ("Blender 4.5", "Blender 5.0", "Blender 4.2"):
        (root / name).mkdir(parents=True)
        (root / name / "blender.exe").write_bytes(b"MZ")
    (root / "Blender 3.6").mkdir()  # папка без exe — не кандидат
    steam = tmp_path / "steamapps" / "common"
    (steam / "Blender").mkdir(parents=True)
    (steam / "Blender" / "blender.exe").write_bytes(b"MZ")
    (steam / "SomeGame").mkdir()
    (steam / "SomeGame" / "game.exe").write_bytes(b"MZ")

    found = find_blender_candidates([root, steam, tmp_path / "missing"])

    assert [Path(p).parent.name for p in found] == [
        "Blender 5.0",
        "Blender 4.5",
        "Blender 4.2",
        "Blender",
    ]


def test_find_candidates_empty_when_nothing_installed(tmp_path: Path) -> None:
    assert find_blender_candidates([tmp_path]) == []


def test_find_candidates_dedupes_repeated_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "Blender 5.0").mkdir(parents=True)
    (root / "Blender 5.0" / "blender.exe").write_bytes(b"MZ")
    assert len(find_blender_candidates([root, root])) == 1
