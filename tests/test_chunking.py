"""Тесты core/chunking.py: пачки и шаги ретрая при нехватке памяти."""
from __future__ import annotations

from brm.core.chunking import MAX_OOM_RETRIES, describe_chunk, oom_retry_overrides, split_chunks


def test_split_chunks() -> None:
    assert split_chunks([1, 2, 3, 4, 5, 6, 7], 3) == [[1, 2, 3], [4, 5, 6], [7]]
    assert split_chunks([1, 2, 3], None) == [[1, 2, 3]]
    assert split_chunks([1, 2, 3], 0) == [[1, 2, 3]]
    assert split_chunks([1, 2, 3], 10) == [[1, 2, 3]]
    assert split_chunks([], 3) == []


def test_oom_retry_overrides_accumulate() -> None:
    note1, first = oom_retry_overrides(1)
    assert first == {"cycles.texture_limit_render": "2048"} and "texture limit" in note1
    note2, second = oom_retry_overrides(2)
    assert second["cycles.tile_size"] == 512 and second["cycles.texture_limit_render"] == "2048"
    assert "tile size" in note2
    _, last = oom_retry_overrides(MAX_OOM_RETRIES)
    assert last["render.use_persistent_data"] is False and last["cycles.denoising_use_gpu"] is False
    assert oom_retry_overrides(MAX_OOM_RETRIES + 1) is None
    assert oom_retry_overrides(0) is None


def test_describe_chunk() -> None:
    assert describe_chunk([]) == "no frames"
    assert describe_chunk([5]) == "frame 5"
    assert describe_chunk([1, 2, 3]) == "frames 1..3 (3)"
