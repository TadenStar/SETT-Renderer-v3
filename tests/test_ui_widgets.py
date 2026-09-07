"""Тесты мелких виджетов интерфейса: нарисованные иконки и сокращающаяся подпись."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSize, Qt

from brm.ui.elided_label import ElidedLabel
from brm.ui.icons import GLYPHS, glyph_icon

LONG_PATH = r"C:\renders\project\sequence\shot_010\scene\beauty_pass_####"


@pytest.mark.parametrize("name", GLYPHS)
def test_every_glyph_draws_something(qapp, name: str) -> None:
    """Пустая иконка на кнопке выглядит как сломанная сборка, а не как «нет иконки»."""
    icon = glyph_icon(name, "#FFFFFF")
    assert not icon.isNull()
    assert not icon.pixmap(QSize(24, 24)).isNull()


def test_unknown_glyph_is_empty_not_a_crash(qapp) -> None:
    """Правило мягкой деградации: неизвестное имя не должно ронять окно."""
    assert glyph_icon("no-such-glyph", "#FFFFFF").isNull()


def test_elided_label_keeps_the_full_text(qapp) -> None:
    label = ElidedLabel(LONG_PATH)
    label.resize(180, 20)
    assert label.full_text() == LONG_PATH
    assert label.toolTip() == LONG_PATH
    shown = label.text()
    assert shown != LONG_PATH and "…" in shown


def test_elided_label_shows_short_text_as_is(qapp) -> None:
    label = ElidedLabel("Scene")
    label.resize(300, 20)
    assert label.text() == "Scene"


def test_elided_label_can_cut_the_head(qapp) -> None:
    """Для строк лога важен хвост: там путь к кадру и причина падения."""
    label = ElidedLabel(LONG_PATH, mode=Qt.TextElideMode.ElideLeft)
    label.resize(180, 20)
    assert label.text().startswith("…") and label.text().endswith("####")
