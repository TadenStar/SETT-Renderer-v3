# BRM — Blender Render Manager

Десктопное приложение под Windows для управления рендером в Blender через CLI.
Спека: `docs/01_BRM_SPEC.md`, настройки рендера и пресеты: `docs/02_RENDER_SETTINGS.md`.

## Запуск из исходников

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m brm
```

Настройки хранятся в `%APPDATA%\BRM\settings.json`.

## Тесты

```powershell
.venv\Scripts\python -m pytest
```

Qt в тестах работает в offscreen-режиме, окна не открываются.

---

Made by Pavel Postnikov
