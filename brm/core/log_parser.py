"""Разбор вывода Blender (раздел 6 спеки). В M2 — только классификация строк;
прогресс, память и ETA появятся в M3 на сохранённых сырых логах.
"""
from __future__ import annotations

import re

# Blender 5.x пишет отчёты в формате CLOG: «00:00.891  reports | ERROR Cannot render, no camera».
RE_ERROR = re.compile(
    r"(Error:|\| ERROR |Traceback \(most recent call last\)|CUDA error|OptiX error|out of memory|"
    r"Cannot read file|\[BRM\] FAIL)"
)
BRM_PREFIX = "[BRM]"


def is_brm_line(line: str) -> bool:
    return line.startswith(BRM_PREFIX)


def is_error_line(line: str) -> bool:
    return RE_ERROR.search(line) is not None
