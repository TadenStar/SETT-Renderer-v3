"""Smoke-тесты UI в offscreen-режиме: три уровня индикации незаданного Blender."""
from __future__ import annotations

from pathlib import Path

from brm.core.storage import AppSettings, SettingsStore
from brm.ui.main_window import MainWindow
from brm.ui.settings_dialog import SettingsDialog


def test_without_blender_banner_visible_and_render_disabled(qapp, settings_path: Path) -> None:
    window = MainWindow(SettingsStore(settings_path))
    assert not window.banner.isHidden()
    assert "Blender не настроен" in window.banner.message()
    assert not window.render_button.isEnabled()
    assert "blender.exe" in window.render_button.toolTip()


def test_with_valid_blender_banner_hidden_and_render_enabled(
    qapp, settings_path: Path, fake_blender: Path
) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender)))
    window = MainWindow(store)
    assert window.banner.isHidden()
    assert window.render_button.isEnabled()
    assert str(fake_blender) in window.blender_label.text()


def test_stale_path_brings_banner_back(qapp, settings_path: Path, fake_blender: Path) -> None:
    store = SettingsStore(settings_path)
    store.save(AppSettings(blender_path=str(fake_blender)))
    window = MainWindow(store)
    assert window.banner.isHidden()

    fake_blender.unlink()  # Blender «переустановили», путь протух
    window.refresh_blender_status()

    assert not window.banner.isHidden()
    assert not window.render_button.isEnabled()
    assert "не найден" in window.render_button.toolTip()


def test_settings_dialog_ok_follows_validation(qapp, fake_blender: Path) -> None:
    dialog = SettingsDialog(AppSettings())
    assert not dialog.ok_button.isEnabled()

    dialog.blender_edit.setText(str(fake_blender))
    assert dialog.ok_button.isEnabled()

    dialog.output_edit.setText(r"D:\out")
    dialog.ffmpeg_edit.setText("   ")
    result = dialog.result_settings()
    assert result.blender_path == str(fake_blender)
    assert result.default_output_dir == r"D:\out"
    assert result.ffmpeg_path is None
