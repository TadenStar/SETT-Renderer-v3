"""Генерация override-скрипта (раздел 3 спеки).

Берёт ``scripts/override_template.py`` и дописывает перед ним ``SETTINGS`` —
JSON с тем, что надо применить. Файл кладётся во временную папку и уходит
Blender'у через ``--python``. Исходный .blend не изменяется никогда.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from brm.core.blender_process import script_path
from brm.core.models import RenderJob

TEMPLATE_NAME = "override_template.py"


def build_override_settings(
    job: RenderJob,
    *,
    scene_name: str,
    engine: str,
    compute_device_type: str | None,
) -> dict[str, Any]:
    """Что override-скрипт должен применить. ``engine`` в SETTINGS только при явной замене."""
    return {
        "scene": scene_name,
        "only_view_layer": job.view_layer,
        "engine": job.engine,
        "disable_sequencer": True,
        "compute_device_type": compute_device_type if engine == "CYCLES" else None,
        "cycles_use_cpu": False,
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
    path = tmp / f"_brm_override_{suffix}{uuid.uuid4().hex[:8]}.py"
    path.write_text(render_override_script(settings), encoding="utf-8")
    return path
