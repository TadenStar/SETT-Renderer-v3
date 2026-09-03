"""Очередь задач (раздел 4.5 спеки): модель, ``queue.json``, последовательный прогон.

Состояние сохраняется после каждого изменения, чтобы пережить перезапуск и
падение: задача, которая была «running» в момент падения, при загрузке
возвращается в «pending». Без Qt.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brm.core.models import RenderJob
from brm.core.project_probe import ProjectInfo
from brm.core.storage import app_data_dir

log = logging.getLogger(__name__)

QUEUE_FILE_NAME = "queue.json"
QueueStatus = Literal["pending", "running", "done", "failed", "stopped", "paused"]
FINISHED_STATUSES = ("done", "failed", "stopped")


class QueueItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    job: RenderJob
    project: ProjectInfo
    status: QueueStatus = "pending"
    message: str = ""
    added_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    frames_done: int = 0
    frames_total: int = 0

    @property
    def title(self) -> str:
        return Path(self.job.blend_path).stem


class RenderQueue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 1
    items: list[QueueItem] = Field(default_factory=list)

    def find(self, item_id: str) -> QueueItem | None:
        return next((item for item in self.items if item.id == item_id), None)

    def next_pending(self) -> QueueItem | None:
        return next((item for item in self.items if item.status == "pending"), None)

    def running(self) -> QueueItem | None:
        return next((item for item in self.items if item.status == "running"), None)

    def add(self, job: RenderJob, project: ProjectInfo) -> QueueItem:
        item = QueueItem(job=job, project=project)
        self.items.append(item)
        return item

    def remove(self, item_ids: list[str]) -> int:
        before = len(self.items)
        self.items = [item for item in self.items if item.id not in item_ids or item.status == "running"]
        return before - len(self.items)

    def clear_finished(self) -> int:
        before = len(self.items)
        self.items = [item for item in self.items if item.status not in FINISHED_STATUSES]
        return before - len(self.items)

    def reset_interrupted(self) -> int:
        """После падения приложения «running» задачи снова становятся «pending»."""
        count = 0
        for item in self.items:
            if item.status == "running":
                item.status = "pending"
                item.message = "interrupted, will restart"
                count += 1
        return count


def queue_path() -> Path:
    return app_data_dir() / QUEUE_FILE_NAME


class QueueStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else queue_path()

    def load(self) -> RenderQueue:
        if not self.path.exists():
            return RenderQueue()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            queue = RenderQueue.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("Failed to read %s: %s", self.path, exc)
            self._quarantine()
            return RenderQueue()
        queue.reset_interrupted()
        return queue

    def save(self, queue: RenderQueue) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(queue.model_dump_json(indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _quarantine(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(self.path, self.path.with_name(f"{self.path.name}.broken-{stamp}"))
        except OSError as exc:
            log.warning("Could not rename corrupt queue file: %s", exc)
