"""Тесты core/frame_range.py: разбор списков кадров и режимы диапазона."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from brm.core.frame_range import (
    FrameRange,
    FrameRangeMode,
    describe_frames,
    format_frame_list,
    parse_frame_list,
    resolve_frames,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,5,10..12", [1, 5, 10, 11, 12]),
        ("10-12", [10, 11, 12]),
        (" 3 , 1 ,2 ", [1, 2, 3]),
        ("5,1,5", [1, 5]),
        ("-3..-1", [-3, -2, -1]),
        ("7", [7]),
        ("1,,2,", [1, 2]),
    ],
)
def test_parse_frame_list(text: str, expected: list[int]) -> None:
    assert parse_frame_list(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "a", "1..b", "5..1", "1;2", "1..2..3"])
def test_parse_frame_list_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_frame_list(text)


@pytest.mark.parametrize(
    ("frames", "expected"),
    [
        ([], ""),
        ([4], "4"),
        ([1, 2], "1,2"),
        ([1, 2, 3], "1..3"),
        ([1, 2, 3, 5, 10, 11, 12, 13, 20], "1..3,5,10..13,20"),
        ([3, 1, 2, 2], "1..3"),
    ],
)
def test_format_frame_list(frames: list[int], expected: str) -> None:
    assert format_frame_list(frames) == expected


def test_format_then_parse_round_trip() -> None:
    frames = [1, 2, 3, 7, 9, 10, 40, 41, 42, 43]
    assert parse_frame_list(format_frame_list(frames)) == frames


def test_resolve_from_file_uses_scene_and_step() -> None:
    fr = FrameRange(mode=FrameRangeMode.FROM_FILE, step=4)
    assert resolve_frames(fr, scene_start=1, scene_end=10) == [1, 5, 9]


def test_resolve_from_file_without_scene_fails() -> None:
    with pytest.raises(ValueError):
        resolve_frames(FrameRange(mode=FrameRangeMode.FROM_FILE))


def test_resolve_manual() -> None:
    fr = FrameRange(mode=FrameRangeMode.MANUAL, start=10, end=14, step=2)
    assert resolve_frames(fr) == [10, 12, 14]


def test_resolve_manual_reversed_fails() -> None:
    fr = FrameRange(mode=FrameRangeMode.MANUAL, start=5, end=1)
    with pytest.raises(ValueError):
        resolve_frames(fr)


def test_resolve_single_ignores_step() -> None:
    fr = FrameRange(mode=FrameRangeMode.SINGLE, frame=42, step=7)
    assert resolve_frames(fr) == [42]


def test_resolve_list() -> None:
    fr = FrameRange(mode=FrameRangeMode.LIST, frames_text="1, 5, 10..12")
    assert resolve_frames(fr) == [1, 5, 10, 11, 12]


def test_step_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        FrameRange(step=0)


def test_describe_frames() -> None:
    assert describe_frames([]) == "no frames"
    assert describe_frames([7]) == "1 frame (7)"
    assert describe_frames([1, 2, 3]) == "3 frames (1..3)"


def test_frame_range_round_trips_through_json() -> None:
    fr = FrameRange(mode=FrameRangeMode.LIST, frames_text="1..3", step=2)
    assert FrameRange.model_validate_json(fr.model_dump_json()) == fr
