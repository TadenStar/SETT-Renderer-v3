"""История рендеров (раздел 4.9 спеки): SQLite, личная база бенчмарков.

Запись строится из уже написанного ``render_stats_<время>.json``
(``core/job_runner.py`` пишет его в конце каждой задачи через
``core/render_stats.write_stats``), поэтому время кадров для графика второй
раз не хранится — читается из того же файла по ``stats_path``. Модуль без Qt.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from brm.core.storage import app_data_dir

log = logging.getLogger(__name__)

HISTORY_FILE_NAME = "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS renders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    blend_path TEXT NOT NULL,
    project TEXT NOT NULL,
    scene TEXT,
    preset TEXT,
    engine TEXT,
    status TEXT NOT NULL,
    frames_total INTEGER NOT NULL,
    frames_done INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    avg_frame_time_s REAL,
    peak_mem_mb REAL,
    log_path TEXT,
    stats_path TEXT NOT NULL
)
"""

_FIELDS = (
    "started_at",
    "finished_at",
    "blend_path",
    "project",
    "scene",
    "preset",
    "engine",
    "status",
    "frames_total",
    "frames_done",
    "duration_s",
    "avg_frame_time_s",
    "peak_mem_mb",
    "log_path",
    "stats_path",
)


def history_db_path() -> Path:
    return app_data_dir() / HISTORY_FILE_NAME


@dataclass
class HistoryEntry:
    id: int | None
    started_at: str
    finished_at: str
    blend_path: str
    project: str
    scene: str | None
    preset: str | None
    engine: str | None
    status: str
    frames_total: int
    frames_done: int
    duration_s: float
    avg_frame_time_s: float | None
    peak_mem_mb: float | None
    log_path: str | None
    stats_path: str


def _average_frame_time(frame_stats: list[dict[str, Any]]) -> float | None:
    values = []
    for stat in frame_stats:
        seconds = stat.get("render_time_s")
        if seconds is None:
            seconds = stat.get("wall_time_s")
        if seconds is not None:
            values.append(seconds)
    return sum(values) / len(values) if values else None


def entry_from_stats(data: dict[str, Any], stats_path: str | os.PathLike[str]) -> HistoryEntry:
    """Строит запись истории из уже написанного render_stats_<время>.json."""
    stats_path = str(stats_path)
    frames_total = len(data.get("frames_expected", []))
    frames_done = len(data.get("frames_done", []))
    frame_stats = data.get("frame_stats", [])
    avg = _average_frame_time(frame_stats)
    duration_s = float(data.get("duration_s") or 0.0)
    if avg is None and frames_done:
        avg = duration_s / frames_done
    blend_path = data.get("blend_path") or ""
    log_files = data.get("log_files") or []
    log_path = str(Path(stats_path).parent / log_files[0]) if log_files else None
    now = datetime.now().isoformat(timespec="seconds")
    return HistoryEntry(
        id=None,
        started_at=data.get("started_at") or now,
        finished_at=data.get("finished_at") or now,
        blend_path=blend_path,
        project=Path(blend_path).stem if blend_path else "",
        scene=data.get("scene"),
        preset=data.get("preset"),
        engine=data.get("engine"),
        status=data.get("status") or "",
        frames_total=frames_total,
        frames_done=frames_done,
        duration_s=duration_s,
        avg_frame_time_s=avg,
        peak_mem_mb=data.get("peak_mem_mb"),
        log_path=log_path,
        stats_path=stats_path,
    )


def read_frame_times(stats_path: str | os.PathLike[str]) -> list[tuple[int, float]]:
    """(кадр, секунды) для графика по прошедшему рендеру. Битый/отсутствующий файл — пустой список."""
    try:
        data = json.loads(Path(stats_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    times: list[tuple[int, float]] = []
    for stat in data.get("frame_stats", []):
        seconds = stat.get("render_time_s")
        if seconds is None:
            seconds = stat.get("wall_time_s")
        frame = stat.get("frame")
        if seconds is not None and frame is not None:
            times.append((int(frame), float(seconds)))
    return times


class HistoryStore:
    """Чтение и запись history.db. Битый файл карантинится, как settings.json."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else history_db_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute(_SCHEMA)
            return conn
        except sqlite3.DatabaseError as exc:
            conn.close()  # иначе Windows не даст переименовать открытый файл
            log.warning("Corrupt history database %s, starting fresh: %s", self.path, exc)
            self._quarantine()
            conn = sqlite3.connect(str(self.path))
            conn.execute(_SCHEMA)
            return conn

    def _quarantine(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(self.path, self.path.with_name(f"{self.path.name}.broken-{stamp}"))
        except OSError as exc:
            log.warning("Could not rename corrupt history database: %s", exc)

    def record(self, entry: HistoryEntry) -> int:
        placeholders = ", ".join("?" for _ in _FIELDS)
        values = tuple(getattr(entry, name) for name in _FIELDS)
        with self._connect() as conn:
            cursor = conn.execute(f"INSERT INTO renders ({', '.join(_FIELDS)}) VALUES ({placeholders})", values)
            return int(cursor.lastrowid)

    def record_from_stats_file(self, stats_path: str | os.PathLike[str]) -> HistoryEntry:
        data = json.loads(Path(stats_path).read_text(encoding="utf-8"))
        entry = entry_from_stats(data, stats_path)
        entry.id = self.record(entry)
        return entry

    def delete_entry(self, entry_id: int) -> bool:
        """Убирает один прогон из истории. False — записи с таким id не было."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM renders WHERE id = ?", (entry_id,))
            return cursor.rowcount > 0

    def clear(self) -> int:
        """Стирает историю целиком и возвращает число удалённых записей."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM renders")
            return int(cursor.rowcount)

    def list_entries(
        self, *, order_by: str = "finished_at", descending: bool = True, limit: int | None = None
    ) -> list[HistoryEntry]:
        if order_by not in _FIELDS and order_by != "id":
            order_by = "finished_at"
        direction = "DESC" if descending else "ASC"
        sql = f"SELECT id, {', '.join(_FIELDS)} FROM renders ORDER BY {order_by} {direction}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [HistoryEntry(id=row[0], **dict(zip(_FIELDS, row[1:], strict=True))) for row in rows]
