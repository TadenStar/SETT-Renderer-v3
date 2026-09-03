"""Тесты core/queue.py: модель очереди и queue.json."""
from __future__ import annotations

import json
from pathlib import Path

from brm.core.models import RenderJob
from brm.core.project_probe import ProjectInfo, SceneInfo
from brm.core.queue import QueueStore, RenderQueue


def make_project(name: str = "cave") -> ProjectInfo:
    return ProjectInfo(file_path=f"D:/shots/{name}.blend", scenes=[SceneInfo(name="Scene", frame_end=10)], active_scene="Scene")


def test_queue_add_next_remove_clear() -> None:
    queue = RenderQueue()
    first = queue.add(RenderJob(blend_path="D:/shots/a.blend", preset="Draft"), make_project("a"))
    second = queue.add(RenderJob(blend_path="D:/shots/b.blend"), make_project("b"))
    assert queue.next_pending() is first and first.title == "a"
    first.status = "done"
    assert queue.next_pending() is second
    second.status = "running"
    assert queue.running() is second
    assert queue.remove([second.id]) == 0  # running не удаляется
    assert queue.remove([first.id]) == 1
    second.status = "failed"
    assert queue.clear_finished() == 1 and queue.items == []


def test_reset_interrupted_marks_running_as_pending() -> None:
    queue = RenderQueue()
    item = queue.add(RenderJob(blend_path="x.blend"), make_project())
    item.status = "running"
    assert queue.reset_interrupted() == 1
    assert item.status == "pending" and "interrupted" in item.message


def test_store_round_trip_and_reset(tmp_path: Path) -> None:
    store = QueueStore(tmp_path / "queue.json")
    assert store.load().items == []
    queue = RenderQueue()
    item = queue.add(RenderJob(blend_path="D:/shots/Пещера.blend", preset="Balanced", overrides={"cycles.samples": 8}), make_project())
    item.status = "running"
    store.save(queue)
    assert not (tmp_path / "queue.json.tmp").exists()

    loaded = store.load()
    assert len(loaded.items) == 1
    restored = loaded.items[0]
    assert restored.id == item.id and restored.status == "pending"  # running → pending после перезапуска
    assert restored.job.preset == "Balanced" and restored.job.overrides == {"cycles.samples": 8}
    assert restored.project.default_scene().frame_end == 10
    assert "Пещера" in (tmp_path / "queue.json").read_text(encoding="utf-8")


def test_corrupt_queue_file_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    path.write_text("{ nope", encoding="utf-8")
    assert QueueStore(path).load().items == []
    assert not path.exists() and list(tmp_path.glob("queue.json.broken-*"))
    path.write_text(json.dumps({"items": "not a list"}), encoding="utf-8")
    assert QueueStore(path).load().items == []
