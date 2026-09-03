"""Тесты core/command_builder.py: порядок аргументов из раздела 5 спеки."""
from __future__ import annotations

import pytest

from brm.core.command_builder import build_argv, command_line, frame_args

BLENDER = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
BLEND = r"D:\Проекты\пещера v3.blend"
OUT = r"D:\out\пещера v3\Scene\####"


def test_frame_args_single_frame() -> None:
    assert frame_args([7]) == ["--render-frame", "7"]


def test_frame_args_contiguous_range() -> None:
    assert frame_args([1, 2, 3]) == ["-s", "1", "-e", "3", "-a"]


def test_frame_args_range_with_step() -> None:
    assert frame_args([1, 5, 9]) == ["-s", "1", "-e", "9", "-j", "4", "-a"]


def test_frame_args_arbitrary_list_uses_render_frame() -> None:
    assert frame_args([1, 5, 10, 11, 12]) == ["--render-frame", "1,5,10..12"]


def test_frame_args_empty_raises() -> None:
    with pytest.raises(ValueError):
        frame_args([])


def test_argv_full_canonical_order() -> None:
    argv = build_argv(
        BLENDER,
        BLEND,
        frames=[1, 2, 3],
        output_path=OUT,
        scene="Scene",
        override_script=r"C:\tmp\_brm_override_x.py",
        threads=0,
        cycles_device="OPTIX",
    )
    assert argv == [
        BLENDER,
        "-b",
        BLEND,
        "-S",
        "Scene",
        "--python-exit-code",
        "1",
        "--python",
        r"C:\tmp\_brm_override_x.py",
        "-o",
        OUT,
        "-F",
        "PNG",
        "-x",
        "1",
        "-t",
        "0",
        "-s",
        "1",
        "-e",
        "3",
        "-a",
        "--",
        "--cycles-device",
        "OPTIX",
        "--cycles-print-stats",
    ]


def test_argv_minimal_without_cycles_section() -> None:
    argv = build_argv(BLENDER, BLEND, frames=[5], output_path=OUT)
    assert argv == [BLENDER, "-b", BLEND, "-o", OUT, "-F", "PNG", "-x", "1", "--render-frame", "5"]
    assert "--" not in argv


def test_factory_startup_precedes_the_file() -> None:
    argv = build_argv(BLENDER, BLEND, frames=[1], output_path=OUT, factory_startup=True)
    assert argv[1:4] == ["-b", "--factory-startup", BLEND]


def test_cycles_print_stats_can_be_disabled() -> None:
    argv = build_argv(BLENDER, BLEND, frames=[1], output_path=OUT, cycles_device="CPU", cycles_print_stats=False)
    assert argv[-3:] == ["--", "--cycles-device", "CPU"]


def test_python_before_output_before_frames() -> None:
    argv = build_argv(BLENDER, BLEND, frames=[1, 2], output_path=OUT, override_script="o.py", file_format="OPEN_EXR")
    assert argv.index("--python") < argv.index("-o") < argv.index("-F") < argv.index("-s") < argv.index("-a")
    assert argv[argv.index("-F") + 1] == "OPEN_EXR"


def test_command_line_quotes_spaces_and_keeps_cyrillic() -> None:
    line = command_line([r"C:\Program Files\b.exe", "-b", r"D:\мой проект\a.blend", "-a"])
    assert line == r'"C:\Program Files\b.exe" -b "D:\мой проект\a.blend" -a'
