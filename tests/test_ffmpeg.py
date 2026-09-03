"""Тесты core/ffmpeg.py: пресеты кодеков, поиск секвенции, сборка команды, прогресс."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.ffmpeg import (
    FfmpegError,
    FfmpegProgress,
    SequenceInfo,
    VideoPreset,
    build_ffmpeg_argv,
    default_output_file,
    find_ffmpeg,
    find_sequence,
    find_video_preset,
    load_video_presets,
    parse_ffmpeg_line,
    preset_slug,
    validate_ffmpeg_path,
)

EXPECTED = ["H.264", "ProRes 422 HQ", "H.265", "Vertical 9:16", "H.264 dark scenes"]


@pytest.fixture
def presets(tmp_path: Path) -> dict[str, VideoPreset]:
    return {p.name: p for p in load_video_presets(user_dir=tmp_path / "none")}


@pytest.fixture
def sequence(tmp_path: Path) -> SequenceInfo:
    out = tmp_path / "shot"
    out.mkdir()
    for frame in range(1, 6):
        (out / f"{frame:04d}.png").write_bytes(b"x" * 100)
    return find_sequence(out / "####")


# --- пресеты ---------------------------------------------------------------------


def test_builtin_video_presets(presets: dict[str, VideoPreset]) -> None:
    assert list(presets) == EXPECTED
    assert all(p.builtin and p.description and p.args for p in presets.values())
    assert presets["H.264"].extension == "mp4" and presets["H.264"].faststart
    assert presets["ProRes 422 HQ"].extension == "mov" and not presets["ProRes 422 HQ"].faststart
    assert "prores_ks" in presets["ProRes 422 HQ"].args and "yuv422p10le" in presets["ProRes 422 HQ"].args
    assert "libx265" in presets["H.265"].args and "hvc1" in presets["H.265"].args
    vertical = presets["Vertical 9:16"]
    assert (vertical.width, vertical.height) == (1080, 1920) and "lanczos" in vertical.video_filter
    # Все H.264/H.265 должны быть yuv420p, иначе Windows и соцсети не проиграют файл.
    for name in ("H.264", "H.265", "Vertical 9:16", "H.264 dark scenes"):
        assert "yuv420p" in presets[name].args, name


def test_user_video_preset_overrides_builtin(tmp_path: Path) -> None:
    user = tmp_path / "video"
    user.mkdir()
    (user / "h264.json").write_text(json.dumps({"name": "H.264", "order": 10, "args": ["-c:v", "mine"]}), encoding="utf-8")
    (user / "mine.json").write_text(json.dumps({"name": "Mine", "order": 1, "args": []}), encoding="utf-8")
    presets = load_video_presets(user_dir=user)
    assert presets[0].name == "Mine" and not presets[0].builtin
    assert find_video_preset(presets, "H.264").args == ["-c:v", "mine"]
    assert len(presets) == len(EXPECTED) + 1


def test_broken_user_video_preset_is_skipped(tmp_path: Path) -> None:
    user = tmp_path / "video"
    user.mkdir()
    (user / "bad.json").write_text("{ nope", encoding="utf-8")
    assert [p.name for p in load_video_presets(user_dir=user)] == EXPECTED
    assert find_video_preset([], "x") is None


def test_output_name() -> None:
    assert VideoPreset(name="X", extension="mov").output_name("shot") == "shot.mov"
    assert VideoPreset(name="X", extension=".mp4").output_name("shot") == "shot.mp4"


# --- путь к ffmpeg -----------------------------------------------------------------


def test_validate_ffmpeg_path(tmp_path: Path) -> None:
    assert not validate_ffmpeg_path(None).ok and "disabled" in validate_ffmpeg_path(None).reason
    assert not validate_ffmpeg_path("   ").ok
    assert "not found" in validate_ffmpeg_path(tmp_path / "nope.exe").reason
    assert "folder" in validate_ffmpeg_path(tmp_path).reason
    other = tmp_path / "ffprobe.exe"
    other.write_bytes(b"MZ")
    assert "Expected ffmpeg" in validate_ffmpeg_path(other).reason
    good = tmp_path / "ffmpeg.exe"
    good.write_bytes(b"MZ")
    status = validate_ffmpeg_path(good)
    assert status.ok and status.path == str(good)
    assert validate_ffmpeg_path(f"  {good}  ").ok  # пробелы по краям режутся


def test_find_ffmpeg_returns_path_or_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert find_ffmpeg() is None

    target = tmp_path / "Microsoft" / "WinGet" / "Links"
    target.mkdir(parents=True)
    exe = target / ("ffmpeg.exe" if __import__("os").name == "nt" else "ffmpeg")
    exe.write_bytes(b"MZ")
    assert find_ffmpeg() == str(exe)

    monkeypatch.setattr("shutil.which", lambda name: r"C:\tools\ffmpeg.exe")
    assert find_ffmpeg() == str(Path(r"C:\tools\ffmpeg.exe"))


# --- секвенция ---------------------------------------------------------------------


def test_find_sequence(sequence: SequenceInfo, tmp_path: Path) -> None:
    assert sequence.frame_count == 5 and sequence.first_frame == 1 and sequence.extension == "png"
    assert sequence.pattern == str(tmp_path / "shot" / "%04d.png")
    assert sequence.directory == tmp_path / "shot"


def test_find_sequence_prefers_the_most_numerous_extension(tmp_path: Path) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    for frame in (1, 2, 3):
        (out / f"{frame:04d}.jpg").write_bytes(b"x")
    (out / "0001.exr").write_bytes(b"x")
    assert find_sequence(out / "####").extension == "jpg"


def test_find_sequence_with_prefix_and_start_number(tmp_path: Path) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    for frame in (10, 11, 12):
        (out / f"cave_{frame:03d}.png").write_bytes(b"x")
    seq = find_sequence(out / "cave_###")
    assert seq.first_frame == 10 and seq.frame_count == 3
    assert seq.pattern == str(out / "cave_%03d.png") and seq.stem == "cave"


def test_find_sequence_stops_at_a_gap(tmp_path: Path) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    for frame in (1, 2, 5, 6):
        (out / f"{frame:04d}.png").write_bytes(b"x")
    assert find_sequence(out / "####").frame_count == 2  # дырка после 2


def test_find_sequence_errors(tmp_path: Path) -> None:
    with pytest.raises(FfmpegError, match="does not exist"):
        find_sequence(tmp_path / "nope" / "####")
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("x")
    with pytest.raises(FfmpegError, match="No rendered frames"):
        find_sequence(empty / "####")


# --- команда ---------------------------------------------------------------------------


def test_build_argv_h264(sequence: SequenceInfo, presets: dict[str, VideoPreset], tmp_path: Path) -> None:
    argv = build_ffmpeg_argv("C:/ff/ffmpeg.exe", sequence, presets["H.264"], tmp_path / "out.mp4", fps=25)
    assert argv[:3] == ["C:/ff/ffmpeg.exe", "-y", "-hide_banner"]
    assert argv[3:5] == ["-framerate", "25"]
    assert "-start_number" not in argv  # секвенция с первого кадра
    assert argv[argv.index("-i") + 1] == sequence.pattern
    assert argv.index("-i") < argv.index("-c:v") < argv.index("-movflags")
    assert argv[argv.index("-crf") + 1] == "17"
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert argv[-1] == str(tmp_path / "out.mp4")


def test_build_argv_prores_has_no_faststart(sequence: SequenceInfo, presets: dict[str, VideoPreset], tmp_path: Path) -> None:
    argv = build_ffmpeg_argv("ffmpeg", sequence, presets["ProRes 422 HQ"], tmp_path / "m.mov", fps=25)
    assert "-movflags" not in argv and "-vf" not in argv
    assert argv[argv.index("-profile:v") + 1] == "3"


def test_build_argv_vertical_adds_scale_filter(sequence: SequenceInfo, presets: dict[str, VideoPreset], tmp_path: Path) -> None:
    argv = build_ffmpeg_argv("ffmpeg", sequence, presets["Vertical 9:16"], tmp_path / "v.mp4", fps=30)
    assert argv[argv.index("-vf") + 1] == "scale=1080:1920:flags=lanczos"
    assert argv.index("-i") < argv.index("-vf") < argv.index("-c:v")
    assert argv[argv.index("-framerate") + 1] == "30"


def test_build_argv_start_number_and_fractional_fps(tmp_path: Path, presets: dict[str, VideoPreset]) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    for frame in (7, 8):
        (out / f"{frame:04d}.png").write_bytes(b"x")
    sequence = find_sequence(out / "####")
    argv = build_ffmpeg_argv("ffmpeg", sequence, presets["H.264"], tmp_path / "o.mp4", fps=23.976, overwrite=False)
    assert argv[1] == "-n"
    assert argv[argv.index("-start_number") + 1] == "7"
    assert argv[argv.index("-framerate") + 1] == "23.976"
    assert argv.index("-framerate") < argv.index("-i")


def test_default_output_file(sequence: SequenceInfo, presets: dict[str, VideoPreset]) -> None:
    path = default_output_file(sequence, presets["H.264"])
    assert path.name == "shot_h264.mp4" and path.parent == sequence.directory.parent
    assert default_output_file(sequence, presets["Vertical 9:16"]).name == "shot_vertical_9_16.mp4"
    assert default_output_file(sequence, presets["ProRes 422 HQ"]).name == "shot_prores_422_hq.mov"


def test_preset_slug() -> None:
    assert preset_slug("H.264") == "h264"
    assert preset_slug("Vertical 9:16") == "vertical_9_16"
    assert preset_slug("H.264 dark scenes") == "h264_dark_scenes"
    assert preset_slug("!!!") == "video"


# --- прогресс --------------------------------------------------------------------------


def test_parse_ffmpeg_progress() -> None:
    progress = FfmpegProgress(total_frames=250)
    assert progress.fraction == 0.0
    assert parse_ffmpeg_line("frame=  125 fps= 45 q=28.0 size=    1024kB time=00:00:05.00 speed=1.8x", progress)
    assert progress.frame == 125 and progress.speed == 1.8 and progress.time == "00:00:05.00"
    assert progress.fraction == 0.5
    assert not parse_ffmpeg_line("  Stream #0:0 -> #0:0 (png -> h264)", progress)
    assert parse_ffmpeg_line("[libx264 @ 0000] Error while opening encoder", progress)
    assert progress.errors == ["[libx264 @ 0000] Error while opening encoder"]
    assert parse_ffmpeg_line("frame=  250 fps= 40", progress)
    assert progress.fraction == 1.0
    assert FfmpegProgress().fraction == 0.0
