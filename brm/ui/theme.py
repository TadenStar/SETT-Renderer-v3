"""Тема оформления (раздел 4.1 спеки): тёмная по умолчанию, светлая, системная.

Стиль Fusion, палитра и короткий QSS с токенами. Минимализм: один акцент,
одна «главная» кнопка, карточки без лишних рамок. Цвета ролей текста
(muted / warning / error / ok) задаются здесь, а виджеты только ставят
свойство ``role`` через ``set_role``.
"""
from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

THEMES = ("dark", "light", "system")
DEFAULT_THEME = "dark"

DARK = {
    "window": "#1B1B1B",
    "card": "#242424",
    "card_border": "#2E2E2E",
    "input": "#1F1F1F",
    "input_border": "#3A3A3A",
    "input_focus": "#2F6FC2",
    "text": "#E6E6E6",
    "muted": "#9A9A9A",
    "disabled": "#6A6A6A",
    "button": "#2E2E2E",
    "button_hover": "#383838",
    "button_border": "#3E3E3E",
    "accent": "#2F6FC2",
    "primary": "#2EBD7A",
    "primary_hover": "#35CC86",
    "primary_text": "#0F1F17",
    "primary_disabled": "#245C42",
    "primary_disabled_text": "#6E8F80",
    "warning": "#E8A33D",
    "error": "#EF6B6B",
    "ok": "#5CC48A",
    "tooltip": "#2A2A2A",
    "highlight": "#2F6FC2",
    "highlight_text": "#FFFFFF",
}

LIGHT = {
    "window": "#F2F2F2",
    "card": "#FFFFFF",
    "card_border": "#E0E0E0",
    "input": "#FFFFFF",
    "input_border": "#C8C8C8",
    "input_focus": "#2F6FC2",
    "text": "#1E1E1E",
    "muted": "#6A6A6A",
    "disabled": "#A0A0A0",
    "button": "#FAFAFA",
    "button_hover": "#EDEDED",
    "button_border": "#C8C8C8",
    "accent": "#2F6FC2",
    "primary": "#2EBD7A",
    "primary_hover": "#28A86C",
    "primary_text": "#FFFFFF",
    "primary_disabled": "#BFE6D2",
    "primary_disabled_text": "#7FA892",
    "warning": "#B85C00",
    "error": "#C62828",
    "ok": "#2E7D32",
    "tooltip": "#FFFFDC",
    "highlight": "#2F6FC2",
    "highlight_text": "#FFFFFF",
}

QSS = Template(
    """
QMainWindow, QDialog { background: $window; }
QGroupBox {
    background: $card;
    border: 1px solid $card_border;
    border-radius: 8px;
    margin-top: 20px;
    padding: 10px 12px 12px 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    color: $muted;
    font-weight: 600;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $input;
    color: $text;
    border: 1px solid $input_border;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: $input_focus; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: $disabled; }
QComboBox QAbstractItemView {
    background: $input;
    color: $text;
    border: 1px solid $input_border;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
}
QPushButton, QToolButton {
    background: $button;
    color: $text;
    border: 1px solid $button_border;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover, QToolButton:hover { background: $button_hover; }
QPushButton:disabled, QToolButton:disabled { color: $disabled; }
QPushButton#primaryButton {
    background: $primary;
    color: $primary_text;
    border: none;
    font-weight: 600;
    padding: 8px 22px;
}
QPushButton#primaryButton:hover { background: $primary_hover; }
QPushButton#primaryButton:disabled { background: $primary_disabled; color: $primary_disabled_text; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="warning"] { color: $warning; }
QLabel[role="error"] { color: $error; }
QLabel[role="ok"] { color: $ok; }
QMenuBar { background: $window; color: $text; }
QMenuBar::item:selected { background: $button_hover; }
QMenu { background: $card; color: $text; border: 1px solid $card_border; }
QMenu::item:selected { background: $highlight; color: $highlight_text; }
QStatusBar { background: $window; }
QStatusBar QLabel { color: $muted; }
QSplitter::handle { background: $window; }
QToolTip { background: $tooltip; color: $text; border: 1px solid $card_border; padding: 4px; }
QScrollBar:vertical { background: $window; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $button_border; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: $window; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $button_border; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""
)


def resolve_theme(name: str, app: QApplication | None = None) -> str:
    """``system`` → ``dark`` или ``light`` по цветовой схеме ОС; неизвестное имя → dark."""
    if name == "system":
        app = app or QApplication.instance()
        try:
            scheme = app.styleHints().colorScheme()  # type: ignore[union-attr]
        except Exception:
            return DEFAULT_THEME
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    return name if name in ("dark", "light") else DEFAULT_THEME


def tokens_for(theme: str) -> dict[str, str]:
    return DARK if theme == "dark" else LIGHT


def build_palette(tokens: dict[str, str]) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "window",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "input",
        QPalette.ColorRole.AlternateBase: "card",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "button",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.ToolTipBase: "tooltip",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.Highlight: "highlight",
        QPalette.ColorRole.HighlightedText: "highlight_text",
        QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.Light: "button_hover",
        QPalette.ColorRole.Midlight: "button",
        QPalette.ColorRole.Mid: "card_border",
        QPalette.ColorRole.Dark: "card_border",
        QPalette.ColorRole.Shadow: "window",
    }
    for role, key in roles.items():
        palette.setColor(role, QColor(tokens[key]))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens["disabled"]))
    return palette


def apply_theme(app: QApplication, name: str) -> str:
    """Ставит стиль, палитру и QSS на всё приложение. Возвращает применённую тему."""
    resolved = resolve_theme(name, app)
    tokens = tokens_for(resolved)
    app.setStyle("Fusion")
    app.setPalette(build_palette(tokens))
    app.setStyleSheet(QSS.substitute(tokens))
    app.setProperty("brm_theme", resolved)
    return resolved


def current_theme(app: QApplication | None = None) -> str | None:
    app = app or QApplication.instance()
    return app.property("brm_theme") if app is not None else None


def set_role(widget: QWidget, role: str) -> None:
    """Семантическая роль текста (muted / warning / error / ok / пусто). Цвет даёт тема."""
    current = widget.property("role")
    if (current or "") == role:
        return
    widget.setProperty("role", role or None)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
