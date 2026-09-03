"""Точка входа: ``python -m brm``."""
from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from brm import __version__
from brm.core.storage import SettingsStore
from brm.ui.main_window import MainWindow
from brm.ui.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("BRM")
    app.setApplicationDisplayName("BRM — Blender Render Manager")
    app.setApplicationVersion(__version__)

    store = SettingsStore()
    apply_theme(app, store.load().theme)
    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
