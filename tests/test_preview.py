"""Тесты core/preview.py: какой кадр можно показать и где взять последний."""
from __future__ import annotations

from pathlib import Path

import pytest

from brm.core.preview import (
    describe_unpreviewable,
    is_previewable,
    latest_rendered_frame,
)


@pytest.mark.parametrize("name", ["0001.png", "shot.JPG", "a.jpeg", "b.bmp", "c.tif", "d.webp", "e.tga"])
def test_previewable_formats(name: str) -> None:
    assert is_previewable(name)


@pytest.mark.parametrize("name", ["0001.exr", "a.hdr", "b.dpx", "c.cin", "d.rgb", "notes.txt", "noext"])
def test_unpreviewable_formats(name: str) -> None:
    assert not is_previewable(name)


def test_describe_unpreviewable_names_the_format() -> None:
    text = describe_unpreviewable(r"D:\out\Scene\0007.exr")
    assert "0007.exr" in text and "OpenEXR" in text and "rendered" in text
    assert "unsupported image format" in describe_unpreviewable("x.psd")


def test_latest_frame_takes_the_highest_number_not_the_newest_file(tmp_path: Path) -> None:
    """После resume дорендеренные кадры моложе, но показать логичнее последний по счёту."""
    out = tmp_path / "Scene"
    out.mkdir()
    for frame in (1, 2, 3):
        (out / f"{frame:04d}.png").write_bytes(b"x")
    import os
    import time

    newer = time.time() + 10
    os.utime(out / "0001.png", (newer, newer))  # кадр 1 перерендерен позже всех

    assert latest_rendered_frame(out / "####") == out / "0003.png"


def test_latest_frame_respects_prefix_and_digits(tmp_path: Path) -> None:
    out = tmp_path / "Scene"
    out.mkdir()
    for frame in (8, 9, 12):
        (out / f"cave_{frame:03d}_v2.png").write_bytes(b"x")
    (out / "other_005.png").write_bytes(b"x")  # чужое имя — не наш кадр
    assert latest_rendered_frame(out / "cave_###_v2") == out / "cave_012_v2.png"


def test_latest_frame_skips_unpreviewable_unless_asked(tmp_path: Path) -> None:
    out = tmp_path / "Scene"
    out.mkdir()
    (out / "0001.png").write_bytes(b"x")
    (out / "0002.exr").write_bytes(b"x")
    assert latest_rendered_frame(out / "####") == out / "0001.png"
    assert latest_rendered_frame(out / "####", previewable_only=False) == out / "0002.exr"


def test_latest_frame_when_nothing_matches(tmp_path: Path) -> None:
    out = tmp_path / "Scene"
    out.mkdir()
    (out / "render_log_20260904-120000.txt").write_text("x", encoding="utf-8")
    assert latest_rendered_frame(out / "####") is None
    assert latest_rendered_frame(tmp_path / "missing" / "####") is None
