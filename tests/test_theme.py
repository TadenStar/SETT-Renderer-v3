"""Тесты ui/theme.py: разрешение имени темы, палитра, роли текста."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel

from brm.ui.theme import DARK, LIGHT, QSS, apply_theme, current_theme, resolve_theme, set_role, tokens_for


@pytest.mark.parametrize(("name", "expected"), [("dark", "dark"), ("light", "light"), ("weird", "dark"), ("", "dark")])
def test_resolve_theme_explicit(qapp, name: str, expected: str) -> None:
    assert resolve_theme(name, qapp) == expected


def test_resolve_system_theme_is_dark_or_light(qapp) -> None:
    assert resolve_theme("system", qapp) in ("dark", "light")


def test_tokens_have_same_keys() -> None:
    assert set(DARK) == set(LIGHT)
    assert tokens_for("dark") is DARK and tokens_for("light") is LIGHT


def test_qss_template_uses_only_known_tokens() -> None:
    rendered = QSS.substitute(DARK)  # KeyError, если в шаблоне есть неизвестный токен
    assert "primaryButton" in rendered
    assert "$" not in rendered


def test_apply_dark_theme_sets_palette_and_stylesheet(qapp) -> None:
    assert apply_theme(qapp, "dark") == "dark"
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(DARK["window"])
    assert qapp.palette().color(QPalette.ColorRole.Base) == QColor(DARK["input"])
    assert DARK["primary"] in qapp.styleSheet()
    assert current_theme(qapp) == "dark"


def test_apply_light_theme_switches_back(qapp) -> None:
    apply_theme(qapp, "light")
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(LIGHT["window"])
    assert current_theme(qapp) == "light"
    apply_theme(qapp, "dark")


def test_set_role_sets_dynamic_property(qapp) -> None:
    label = QLabel("x")
    assert label.property("role") is None
    set_role(label, "warning")
    assert label.property("role") == "warning"
    set_role(label, "warning")  # повтор не ломает
    assert label.property("role") == "warning"
    set_role(label, "")
    assert not label.property("role")
