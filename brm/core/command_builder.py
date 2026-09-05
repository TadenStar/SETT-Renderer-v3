"""Сборка argv для blender.exe (раздел 5 спеки). Порядок аргументов критичен.

Каноническая форма:
    blender -b [--factory-startup] file.blend [-S Scene]
            [--python-exit-code 1 --python override.py]
            -o out/#### [-F PNG] -x 1 [-t N]
            (-s A -e B [-j S] -a | --render-frame 1,5,10..20)
            [-- --cycles-device OPTIX --cycles-print-stats]
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence

from brm.core.frame_range import format_frame_list


def frame_args(frames: Sequence[int]) -> list[str]:
    """Диапазон с шагом → ``-s/-e/-j -a``; всё остальное → ``--render-frame``."""
    if not frames:
        raise ValueError("No frames to render")
    if len(frames) == 1:
        return ["--render-frame", str(frames[0])]
    step = frames[1] - frames[0]
    contiguous = step >= 1 and all(b - a == step for a, b in zip(frames, frames[1:]))
    if not contiguous:
        return ["--render-frame", format_frame_list(list(frames))]
    args = ["-s", str(frames[0]), "-e", str(frames[-1])]
    if step != 1:
        args += ["-j", str(step)]
    args.append("-a")
    return args


def build_argv(
    blender_path: str | os.PathLike[str],
    blend_path: str | os.PathLike[str],
    *,
    frames: Sequence[int],
    output_path: str | os.PathLike[str],
    scene: str | None = None,
    override_script: str | os.PathLike[str] | None = None,
    file_format: str | None = None,
    threads: int | None = None,
    factory_startup: bool = False,
    cycles_device: str | None = None,
    # По умолчанию выключено: таблица текстур занимает ~330 строк на кадр,
    # раздувает лог и врезается в строки прогресса. Устройство и так видно
    # в логе по строкам [BRM] от override-скрипта.
    cycles_print_stats: bool = False,
) -> list[str]:
    """Список аргументов. Никакой сборки строкой: пути с пробелами и кириллицей."""
    argv = [str(blender_path), "-b"]
    if factory_startup:
        argv.append("--factory-startup")
    argv.append(str(blend_path))
    if scene:
        argv += ["-S", scene]
    if override_script is not None:
        # Если override упал, Blender не должен молча рендерить с чужими настройками.
        argv += ["--python-exit-code", "1", "--python", str(override_script)]
    argv += ["-o", str(output_path)]
    # Без -F Blender берёт формат из сцены. Навязывать свой можно только
    # по явному выбору: иначе приложение переписывает то, что художник
    # настроил в файле, включая вывод композитора.
    if file_format:
        argv += ["-F", file_format]
    argv += ["-x", "1"]
    if threads is not None:
        argv += ["-t", str(threads)]
    argv += frame_args(frames)
    if cycles_device:
        argv += ["--", "--cycles-device", cycles_device]
        if cycles_print_stats:
            argv.append("--cycles-print-stats")
    return argv


def command_line(argv: Sequence[str]) -> str:
    """Строка для панели «Command» и кнопки Copy: кавычки как у Windows."""
    return subprocess.list2cmdline([str(a) for a in argv])
