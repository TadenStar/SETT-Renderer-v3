"""Главное окно. Только отображение: статус Blender спрашиваем у core."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from brm import __author__, __version__
from brm.core.blender_locator import validate_blender_path
from brm.core.storage import SettingsStore
from brm.ui.banner import WarningBanner
from brm.ui.log_view import LogView
from brm.ui.project_panel import ProjectPanel
from brm.ui.queue_view import QueueView
from brm.ui.settings_dialog import SettingsDialog
from brm.ui.settings_form import SettingsForm

CREDIT_TEXT = f"Made by {__author__}"


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.settings = store.load()

        self.setWindowTitle(f"BRM — Blender Render Manager {__version__}")
        self.resize(1200, 760)

        self._build_menu()
        self._build_central()
        self.refresh_blender_status()

    # --- построение ----------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        self.settings_action = QAction("&Settings…", self)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(self.settings_action)

        file_menu.addSeparator()
        quit_action = QAction("E&xit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About BRM", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _build_central(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.banner = WarningBanner(central)
        self.banner.action_clicked.connect(self.open_settings)
        root.addWidget(self.banner)

        top = QWidget(central)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 8, 12, 8)
        self.blender_label = QLabel(top)
        top_layout.addWidget(self.blender_label, 1)
        self.render_button = QPushButton("Render", top)
        self.render_button.setMinimumWidth(120)
        top_layout.addWidget(self.render_button, 0)
        root.addWidget(top)

        self.project_panel = ProjectPanel()
        self.settings_form = SettingsForm()
        self.log_view = LogView()
        self.queue_view = QueueView()

        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.project_panel)
        left.addWidget(self.settings_form)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.log_view)
        right.addWidget(self.queue_view)
        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(left)
        main_split.addWidget(right)
        main_split.setSizes([480, 720])
        root.addWidget(main_split, 1)

        self.setCentralWidget(central)

        # Подпись автора — постоянный виджет справа в статус-баре.
        self.credit_label = QLabel(CREDIT_TEXT, self)
        self.statusBar().addPermanentWidget(self.credit_label)

    # --- состояние -----------------------------------------------------------

    def refresh_blender_status(self) -> None:
        """Три уровня индикации из раздела 4.1: кнопка, тултип, баннер."""
        status = validate_blender_path(self.settings.blender_path)
        if status.ok:
            self.banner.hide()
            self.render_button.setEnabled(True)
            self.render_button.setToolTip("Start render (coming in M2)")
            self.blender_label.setText(f"Blender: {status.path}")
        else:
            self.banner.set_message(f"Blender is not configured: {status.reason}")
            self.banner.show()
            self.render_button.setEnabled(False)
            self.render_button.setToolTip(status.reason)
            self.blender_label.setText("Blender is not configured")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.focus_blender_path()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.result_settings()
            self._store.save(self.settings)
            self.refresh_blender_status()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About BRM",
            f"<b>BRM — Blender Render Manager</b><br>Version {__version__}<br><br>{CREDIT_TEXT}",
        )

    def event(self, event: QEvent) -> bool:
        # Путь мог протухнуть, пока окно было в фоне (переустановили Blender).
        if event.type() == QEvent.Type.WindowActivate and hasattr(self, "render_button"):
            self.refresh_blender_status()
        return super().event(event)
