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

Интеграционные тесты с маркером `blender` запускают настоящий `blender.exe`:
он ищется автоматически (Blender Foundation, Steam) или берётся из переменной
`BRM_BLENDER`. Без Blender они пропускаются. Только их: `pytest -m blender`.

Тест сборки видео дополнительно требует `ffmpeg`: он ищется в PATH и типичных
папках установки либо берётся из `BRM_FFMPEG`. Без него тест пропускается.

## ffmpeg

Нужен только для сборки секвенции в видео (панель Video). Без него рендер
работает полностью, отключается лишь последний шаг. Установка:

```powershell
winget install Gyan.FFmpeg
```

Затем File → Settings → ffmpeg → Auto-detect.

Сохранённые выводы проб для юнит-тестов лежат в `tests/fixtures/`. Кэш
capabilities и временные файлы приложения — в `%LOCALAPPDATA%\BRM\`.

---

Made by Pavel Postnikov
