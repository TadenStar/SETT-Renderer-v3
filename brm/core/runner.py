"""Запуск одного процесса Blender через QProcess (раздел 4.4 спеки).

Место в core с импортом Qt: спека требует QProcess для живого чтения stdout
без подвисаний UI. Задача целиком (пачки, resume, авторетрай) — в ``job_runner``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from brm.core.render_plan import RenderPlan

log = logging.getLogger(__name__)

# Blender перезаписывает строку прогресса через \r — читаем по обоим разделителям.
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_CRASHED = "crashed"


class RenderProcess(QObject):
    """Один процесс Blender: старт по плану, построчный вывод, Stop, лог в файл."""

    started = Signal()
    line_received = Signal(str)
    finished = Signal(int, str)  # код выхода, статус

    def __init__(self, parent: QObject | None = None, *, kill_delay_ms: int = 5000) -> None:
        super().__init__(parent)
        self._kill_delay_ms = kill_delay_ms
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_ready_read)
        self._proc.started.connect(self.started)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        self._buffer = ""
        self._log_file = None
        self._stop_requested = False
        self._reported = False
        self.plan: RenderPlan | None = None
        self.started_at: datetime | None = None
        self.exit_code: int | None = None
        self.status: str | None = None

    # --- управление ---------------------------------------------------------------

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, plan: RenderPlan) -> None:
        if self.is_running():
            raise RuntimeError("A render is already running")
        self.plan = plan
        self._buffer = ""
        self._stop_requested = False
        self._reported = False
        self.exit_code = None
        self.status = None
        self.started_at = datetime.now()

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self._proc.setProcessEnvironment(env)
        self._proc.setProgram(plan.argv[0])
        self._proc.setArguments(plan.argv[1:])
        self._open_log(plan)
        self._proc.start()

    def stop(self, *, force: bool = False) -> None:
        """Стоп: terminate, через kill_delay_ms — kill, если Blender не вышел.

        ``force`` убивает сразу: после «Saved:» кадр на диске и терять нечего,
        а Blender в фоне на terminate всё равно не реагирует.
        """
        if not self.is_running():
            return
        self._stop_requested = True
        if force:
            self._proc.kill()
            return
        self._proc.terminate()
        QTimer.singleShot(self._kill_delay_ms, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.is_running():
            self._proc.kill()

    # --- вывод -------------------------------------------------------------------------

    def _on_ready_read(self) -> None:
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer += data
        parts = _LINE_SPLIT.split(self._buffer)
        self._buffer = parts.pop()  # хвост без разделителя ждёт продолжения
        for line in parts:
            self._emit_line(line)

    def _emit_line(self, line: str) -> None:
        if self._log_file is not None:
            try:
                self._log_file.write(line + "\n")
            except OSError as exc:
                log.warning("Cannot write render log: %s", exc)
                self._log_file = None
        self.line_received.emit(line)

    def _flush(self) -> None:
        if self._buffer:
            tail, self._buffer = self._buffer, ""
            self._emit_line(tail)

    # --- завершение ------------------------------------------------------------------

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._flush()
        if self._stop_requested:
            status = STATUS_STOPPED
        elif exit_status == QProcess.ExitStatus.CrashExit:
            status = STATUS_CRASHED
        elif exit_code == 0:
            status = STATUS_SUCCESS
        else:
            status = STATUS_FAILED
        self._report(exit_code, status)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        # FailedToStart не даёт сигнала finished — закрываем сами.
        if error == QProcess.ProcessError.FailedToStart:
            self._emit_line(f"[BRM] FAIL could not start {self._proc.program()}: {self._proc.errorString()}")
            self._report(-1, STATUS_CRASHED)

    def _report(self, exit_code: int, status: str) -> None:
        if self._reported:
            return
        self._reported = True
        self.exit_code = exit_code
        self.status = status
        self._close_log(exit_code, status)
        self.finished.emit(exit_code, status)

    # --- лог-файл ---------------------------------------------------------------------

    def _open_log(self, plan: RenderPlan) -> None:
        try:
            plan.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = plan.log_path.open("w", encoding="utf-8")
            self._log_file.write(f"# BRM render log, started {self.started_at:%Y-%m-%d %H:%M:%S}\n")
            self._log_file.write(f"# command: {plan.command_line}\n")
        except OSError as exc:
            log.warning("Cannot open render log %s: %s", plan.log_path, exc)
            self._log_file = None

    def _close_log(self, exit_code: int, status: str) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.write(f"# finished: status={status} exit_code={exit_code}\n")
            self._log_file.close()
        except OSError as exc:
            log.warning("Cannot close render log: %s", exc)
        self._log_file = None
