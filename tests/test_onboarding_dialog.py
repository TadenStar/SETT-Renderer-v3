"""Тесты ui/onboarding_dialog.py: тот же протокол, что и SettingsDialog, плюс приветствие."""
from __future__ import annotations

from pathlib import Path

from brm.core.storage import AppSettings
from brm.ui.onboarding_dialog import OnboardingDialog, WELCOME_TEXT
from brm.ui.settings_dialog import SettingsDialog


def test_onboarding_is_a_settings_dialog_with_a_welcome_header(qapp) -> None:
    dialog = OnboardingDialog(AppSettings())
    assert isinstance(dialog, SettingsDialog)
    assert dialog.windowTitle() == "Welcome to BRM"
    assert not dialog.ok_button.isEnabled()  # путь ещё не задан


def test_welcome_label_is_the_first_widget(qapp) -> None:
    dialog = OnboardingDialog(AppSettings())
    first_item = dialog.layout().itemAt(0)
    assert first_item.widget().text() == WELCOME_TEXT


def test_blender_path_validation_and_result_are_inherited(qapp, fake_blender: Path) -> None:
    dialog = OnboardingDialog(AppSettings())
    dialog.blender_edit.setText(str(fake_blender))
    assert dialog.ok_button.isEnabled()
    result = dialog.result_settings()
    assert result.blender_path == str(fake_blender)
    assert result.onboarding_seen is False  # флаг ставит вызывающий код, не сам диалог
