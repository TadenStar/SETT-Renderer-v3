"""Тесты core/output_scan.py: шаблон вывода, поиск кадров, порог размера."""
from __future__ import annotations

from pathlib import Path

from brm.core.output_scan import extension_for_format, parse_output_template, scan_output


def test_parse_output_template_hashes() -> None:
    tpl = parse_output_template(r"D:\out\shot\####")
    assert (tpl.prefix, tpl.suffix, tpl.digits) == ("", "", 4)
    assert tpl.directory == Path(r"D:\out\shot")
    assert tpl.file_stem(7) == "0007"

    tpl = parse_output_template("D:/out/shot_###_v2")
    assert (tpl.prefix, tpl.suffix, tpl.digits) == ("shot_", "_v2", 3)
    assert tpl.file_stem(12) == "shot_012_v2"


def test_parse_output_template_without_hashes_appends_four_digits() -> None:
    tpl = parse_output_template("D:/out/frame")
    assert (tpl.prefix, tpl.digits) == ("frame", 4)
    assert tpl.file_stem(1) == "frame0001"


def test_extension_for_format() -> None:
    assert extension_for_format("PNG") == "png"
    assert extension_for_format("OPEN_EXR_MULTILAYER") == "exr"
    assert extension_for_format("jpeg") == "jpg"
    assert extension_for_format("NOPE") is None and extension_for_format(None) is None


def test_scan_finds_existing_and_undersized_frames(tmp_path: Path) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    (out / "0001.png").write_bytes(b"x" * 5000)
    (out / "0002.png").write_bytes(b"x" * 10)  # битый
    (out / "0004.exr").write_bytes(b"x" * 5000)  # другой формат
    scan = scan_output(out / "####", [1, 2, 3, 4], extensions=["png"])
    assert scan.existing() == [1, 2]
    assert scan.missing([1, 2, 3, 4]) == [3, 4]
    assert scan.missing([1, 2, 3, 4], min_size_bytes=1024) == [2, 3, 4]
    assert scan.undersized(1024) == [2]
    assert scan.files[1].size == 5000 and scan.files[3].path is None

    any_format = scan_output(out / "####", [4])
    assert any_format.existing() == [4]


def test_scan_prefers_largest_candidate(tmp_path: Path) -> None:
    out = tmp_path / "shot"
    out.mkdir()
    (out / "0001.jpg").write_bytes(b"x" * 100)
    (out / "0001.exr").write_bytes(b"x" * 900)
    scan = scan_output(out / "####", [1])
    assert scan.files[1].path == out / "0001.exr" and scan.files[1].size == 900


def test_scan_missing_directory(tmp_path: Path) -> None:
    scan = scan_output(tmp_path / "nope" / "####", [1, 2])
    assert scan.missing([1, 2]) == [1, 2] and scan.existing() == []


def test_scan_with_prefix_and_special_characters(tmp_path: Path) -> None:
    out = tmp_path / "пещера [v3]"
    out.mkdir()
    (out / "cave_0010.png").write_bytes(b"x" * 10)
    scan = scan_output(out / "cave_####", [10, 11])
    assert scan.existing() == [10]
