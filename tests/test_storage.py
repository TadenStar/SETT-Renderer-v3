"""Тесты core/storage.py: чтение, запись, восстановление после битого файла."""
from __future__ import annotations

import json
from pathlib import Path

from brm.core.storage import AppSettings, SettingsStore, default_settings_path


def test_load_returns_defaults_when_file_missing(settings_path: Path) -> None:
    settings = SettingsStore(settings_path).load()
    assert settings == AppSettings()
    assert settings.blender_path is None
    assert not settings_path.exists()


def test_save_then_load_round_trip_with_cyrillic(settings_path: Path) -> None:
    store = SettingsStore(settings_path)
    original = AppSettings(
        blender_path=r"C:\Программы\Blender 4.5\blender.exe",
        ffmpeg_path=None,
        default_output_dir=r"D:\Рендер\выход",
        shutdown_after_queue=True,
        theme="dark",
    )
    store.save(original)
    assert store.load() == original
    # Кириллица пишется как есть, а не \uXXXX — файл читаем глазами.
    assert "Программы" in settings_path.read_text(encoding="utf-8")


def test_save_creates_parent_dirs_and_leaves_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "settings.json"
    SettingsStore(path).save(AppSettings())
    assert path.exists()
    assert sorted(p.name for p in path.parent.iterdir()) == ["settings.json"]


def test_save_overwrites_existing_file(settings_path: Path) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path="one"))
    store.save(AppSettings(blender_path="two"))
    assert store.load().blender_path == "two"


def test_corrupt_json_gives_defaults_and_quarantines_file(settings_path: Path) -> None:
    settings_path.write_text("{ this is not json", encoding="utf-8")
    settings = SettingsStore(settings_path).load()
    assert settings == AppSettings()
    assert not settings_path.exists()
    broken = list(settings_path.parent.glob("settings.json.broken-*"))
    assert len(broken) == 1
    assert broken[0].read_text(encoding="utf-8") == "{ this is not json"


def test_invalid_value_is_treated_as_corrupt(settings_path: Path) -> None:
    settings_path.write_text(json.dumps({"theme": "neon"}), encoding="utf-8")
    assert SettingsStore(settings_path).load() == AppSettings()
    assert list(settings_path.parent.glob("settings.json.broken-*"))


def test_json_root_not_object_is_corrupt(settings_path: Path) -> None:
    settings_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert SettingsStore(settings_path).load() == AppSettings()
    assert not settings_path.exists()


def test_unknown_keys_are_ignored(settings_path: Path) -> None:
    settings_path.write_text(
        json.dumps({"blender_path": "x", "future_option": 42}), encoding="utf-8"
    )
    settings = SettingsStore(settings_path).load()
    assert settings.blender_path == "x"
    assert not hasattr(settings, "future_option")


def test_default_path_uses_appdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_settings_path() == tmp_path / "BRM" / "settings.json"
    assert SettingsStore().path == tmp_path / "BRM" / "settings.json"


def test_default_path_without_appdata_falls_back_to_home(monkeypatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    path = default_settings_path()
    assert path.parts[-3:] == ("Roaming", "BRM", "settings.json")
