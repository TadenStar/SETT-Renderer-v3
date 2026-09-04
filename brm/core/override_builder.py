"""Генерация override-скрипта (раздел 3 спеки).

Берёт ``scripts/override_template.py`` и дописывает перед ним ``SETTINGS`` —
JSON с тем, что надо применить. Файл кладётся во временную папку и уходит
Blender'у через ``--python``. Исходный .blend не изменяется никогда.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from brm.core.blender_process import script_path
from brm.core.models import RenderJob

TEMPLATE_NAME = "override_template.py"
OVERRIDE_PREFIX = "_brm_override_"


def build_override_settings(
    job: RenderJob,
    *,
    scene_name: str,
    engine: str,
    compute_device_type: str | None,
    use_cpu: bool = False,
) -> dict[str, Any]:
    """Что override-скрипт должен применить. ``engine`` в SETTINGS только при явной замене."""
    return {
        "scene": scene_name,
        "only_view_layer": job.view_layer,
        "engine": job.engine,
        "disable_sequencer": True,
        "compute_device_type": compute_device_type if engine == "CYCLES" else None,
        "cycles_use_cpu": use_cpu,
        # Галка на сцене ничего не режет без флага у объектов — включает override.
        "camera_cull_objects": bool(job.camera_cull) and engine == "CYCLES",
        "assignments": [[path, value] for path, value in job.overrides.items()],
        "strict": False,
    }


def render_override_script(settings: dict[str, Any]) -> str:
    """Текст скрипта: заголовок с SETTINGS плюс шаблон целиком."""
    template = script_path(TEMPLATE_NAME).read_text(encoding="utf-8")
    payload = json.dumps(settings, ensure_ascii=False, indent=1)
    header = (
        "# Сгенерировано BRM. Временный файл, исходный .blend не изменяется.\n"
        "import json\n"
        f"SETTINGS = json.loads({payload!r})\n\n"
    )
    return header + template


def write_override_script(
    settings: dict[str, Any], tmp_dir: str | os.PathLike[str], job_id: str = ""
) -> Path:
    """Пишет скрипт в ``tmp_dir/_brm_override_<job>_<uuid>.py`` и возвращает путь."""
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    suffix = f"{job_id}_" if job_id else ""
    path = tmp / f"{OVERRIDE_PREFIX}{suffix}{uuid.uuid4().hex[:8]}.py"
    path.write_text(render_override_script(settings), encoding="utf-8")
    return path


def prune_override_scripts(
    tmp_dir: str | os.PathLike[str], *, max_age_hours: float = 48.0, keep_last: int = 20
) -> int:
    """Убирает старые override-скрипты, возвращает число удалённых.

    Скрипт не удаляется сразу после рендера намеренно: по нему видно, что именно
    BRM сказал Blender'у. Но каждая пачка пишет свой файл, поэтому за месяцы
    ночных рендеров их накапливаются тысячи — свежие оставляем, старые чистим.
    Всё делается best-effort: занятый или чужой файл просто пропускаем.
    """
    directory = Path(tmp_dir)
    if not directory.is_dir():
        return 0
    try:
        scripts = [p for p in directory.glob(f"{OVERRIDE_PREFIX}*.py") if p.is_file()]
    except OSError:
        return 0
    if len(scripts) <= keep_last:
        return 0
    try:
        scripts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    deadline = time.time() - max_age_hours * 3600
    removed = 0
    for script in scripts[keep_last:]:  # самые свежие keep_last не трогаем в любом случае
        try:
            if script.stat().st_mtime >= deadline:
                continue
            script.unlink()
        except OSError:
            continue
        removed += 1
    return removed
