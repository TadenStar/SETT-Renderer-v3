"""Тесты core/history.py: SQLite-запись, чтение, сортировка, битый файл."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from brm.core.history import (
    HistoryEntry,
    HistoryStore,
    entry_from_stats,
    history_db_path,
    read_frame_times,
)


def make_stats(**overrides) -> dict:
    data = {
        "status": "success",
        "exit_code": 0,
        "duration_s": 12.5,
        "engine": "CYCLES",
        "frames_expected": [1, 2, 3],
        "frames_done": [1, 2, 3],
        "peak_mem_mb": 512.0,
        "frame_stats": [
            {"frame": 1, "render_time_s": 4.0, "wall_time_s": 4.2, "saved_path": "x/0001.png"},
            {"frame": 2, "render_time_s": 4.1, "wall_time_s": 4.3, "saved_path": "x/0002.png"},
            {"frame": 3, "render_time_s": 4.2, "wall_time_s": 4.4, "saved_path": "x/0003.png"},
        ],
        "errors": [],
        "blend_path": r"D:\shots\Пещера.blend",
        "scene": "Scene",
        "preset": "Balanced",
        "chunks": 1,
        "retries": [],
        "skipped_existing": [],
        "log_files": ["render_log_20260904-120000.txt"],
        "output_path": r"D:\out\cave\Scene\####",
        "started_at": "2026-09-04T12:00:00",
        "finished_at": "2026-09-04T12:00:12",
    }
    data.update(overrides)
    return data


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


def write_stats_file(tmp_path: Path, name: str, **overrides) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(make_stats(**overrides)), encoding="utf-8")
    return path


# --- entry_from_stats / read_frame_times --------------------------------------------


def test_entry_from_stats_computes_project_and_average() -> None:
    entry = entry_from_stats(make_stats(), "D:/out/cave/Scene/render_stats_x.json")
    assert entry.project == "Пещера"
    assert entry.frames_total == 3 and entry.frames_done == 3
    assert entry.avg_frame_time_s == pytest.approx(4.1)  # среднее render_time_s
    assert entry.log_path == str(Path("D:/out/cave/Scene") / "render_log_20260904-120000.txt")
    assert entry.started_at == "2026-09-04T12:00:00" and entry.finished_at == "2026-09-04T12:00:12"
    assert entry.id is None


def test_entry_from_stats_falls_back_to_wall_time_and_duration() -> None:
    data = make_stats(frame_stats=[{"frame": 1, "render_time_s": None, "wall_time_s": 5.0}])
    assert entry_from_stats(data, "x.json").avg_frame_time_s == 5.0

    data = make_stats(frame_stats=[], duration_s=20.0, frames_done=[1, 2, 3, 4])
    assert entry_from_stats(data, "x.json").avg_frame_time_s == 5.0

    data = make_stats(frame_stats=[], duration_s=20.0, frames_done=[])
    assert entry_from_stats(data, "x.json").avg_frame_time_s is None


def test_entry_from_stats_handles_missing_optional_fields() -> None:
    entry = entry_from_stats({"status": "failed"}, "x.json")
    assert entry.blend_path == "" and entry.project == ""
    assert entry.frames_total == 0 and entry.frames_done == 0
    assert entry.log_path is None
    assert entry.started_at and entry.finished_at  # заполнены текущим временем


def test_read_frame_times(tmp_path: Path) -> None:
    path = write_stats_file(tmp_path, "render_stats_1.json")
    assert read_frame_times(path) == [(1, 4.0), (2, 4.1), (3, 4.2)]


def test_read_frame_times_falls_back_to_wall_time(tmp_path: Path) -> None:
    data = make_stats(frame_stats=[{"frame": 5, "render_time_s": None, "wall_time_s": 2.5}])
    path = tmp_path / "s.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_frame_times(path) == [(5, 2.5)]


def test_read_frame_times_missing_or_broken_file_is_empty(tmp_path: Path) -> None:
    assert read_frame_times(tmp_path / "nope.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert read_frame_times(broken) == []


# --- HistoryStore ------------------------------------------------------------------


def test_record_and_list_round_trip(store: HistoryStore, tmp_path: Path) -> None:
    stats_path = write_stats_file(tmp_path, "render_stats_1.json")
    entry = store.record_from_stats_file(stats_path)
    assert entry.id == 1

    entries = store.list_entries()
    assert len(entries) == 1
    loaded = entries[0]
    assert loaded.id == 1
    assert loaded.project == "Пещера"
    assert loaded.preset == "Balanced" and loaded.scene == "Scene" and loaded.engine == "CYCLES"
    assert loaded.frames_total == 3 and loaded.frames_done == 3
    assert loaded.duration_s == 12.5 and loaded.peak_mem_mb == 512.0
    assert loaded.avg_frame_time_s == pytest.approx(4.1)
    assert loaded.status == "success"


def test_list_entries_sorted_newest_first_by_default(store: HistoryStore, tmp_path: Path) -> None:
    for hour, name in ((10, "a"), (14, "b"), (12, "c")):
        path = write_stats_file(tmp_path, f"{name}.json", finished_at=f"2026-09-04T{hour:02d}:00:00", preset=name)
        store.record_from_stats_file(path)
    presets = [e.preset for e in store.list_entries()]
    assert presets == ["b", "c", "a"]


def test_list_entries_order_by_and_limit(store: HistoryStore, tmp_path: Path) -> None:
    for done, name in ((1, "a"), (5, "b"), (3, "c")):
        path = write_stats_file(tmp_path, f"{name}.json", frames_done=list(range(done)), preset=name)
        store.record_from_stats_file(path)
    ascending = [e.preset for e in store.list_entries(order_by="frames_done", descending=False)]
    assert ascending == ["a", "c", "b"]
    assert len(store.list_entries(limit=2)) == 2


def test_order_by_unknown_column_falls_back_to_finished_at(store: HistoryStore, tmp_path: Path) -> None:
    path = write_stats_file(tmp_path, "a.json")
    store.record_from_stats_file(path)
    entries = store.list_entries(order_by="'; DROP TABLE renders; --")
    assert len(entries) == 1  # не упало и не выполнило постороннее


def test_multiple_records_accumulate(store: HistoryStore, tmp_path: Path) -> None:
    for i in range(3):
        path = write_stats_file(tmp_path, f"r{i}.json", finished_at=f"2026-09-0{i + 1}T10:00:00")
        store.record_from_stats_file(path)
    assert len(store.list_entries()) == 3


def test_corrupt_database_is_quarantined_and_recreated(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    db_path.write_text("not a real sqlite file", encoding="utf-8")
    store = HistoryStore(db_path)
    stats_path = write_stats_file(tmp_path, "render_stats_1.json")
    entry = store.record_from_stats_file(stats_path)
    assert entry.id == 1
    assert not db_path.read_bytes().startswith(b"not a real")
    broken = list(tmp_path.glob("history.db.broken-*"))
    assert len(broken) == 1 and broken[0].read_text(encoding="utf-8") == "not a real sqlite file"


def test_record_from_stats_file_missing_file_raises(store: HistoryStore, tmp_path: Path) -> None:
    with pytest.raises(OSError):
        store.record_from_stats_file(tmp_path / "nope.json")


def test_history_db_path_uses_appdata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert history_db_path() == tmp_path / "BRM" / "history.db"
    assert HistoryStore().path == tmp_path / "BRM" / "history.db"


def test_cyrillic_paths_are_stored_as_is(store: HistoryStore, tmp_path: Path) -> None:
    path = write_stats_file(tmp_path, "r.json", blend_path=r"D:\Рендер\пещера v3.blend")
    entry = store.record_from_stats_file(path)
    assert entry.project == "пещера v3"
    reloaded = store.list_entries()[0]
    assert reloaded.blend_path == r"D:\Рендер\пещера v3.blend"


def test_delete_one_entry_and_clear_everything(tmp_path: Path) -> None:
    """Отзыв: нужна очистка всей истории и удаление отдельного рендера."""
    store = HistoryStore(tmp_path / "history.db")
    ids = [
        store.record(entry_from_stats(make_stats(scene=f"S{i}"), tmp_path / f"stats{i}.json"))
        for i in range(3)
    ]

    assert store.delete_entry(ids[1]) is True
    assert store.delete_entry(ids[1]) is False  # второй раз удалять нечего
    assert [e.id for e in store.list_entries(order_by="id", descending=False)] == [ids[0], ids[2]]

    assert store.clear() == 2
    assert store.list_entries() == []
    assert store.clear() == 0
