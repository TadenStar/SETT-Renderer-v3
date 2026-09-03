"""Тесты core/system_actions.py. Настоящий shutdown не вызывается: подменяется subprocess.run."""
from __future__ import annotations

import subprocess

import pytest

from brm.core import system_actions
from brm.core.system_actions import SHUTDOWN_DELAY_S, cancel_shutdown, is_supported, schedule_shutdown


class FakeRun:
    def __init__(self, returncode: int = 0, stderr: str = "", exc: Exception | None = None) -> None:
        self.returncode, self.stderr, self.exc = returncode, stderr, exc
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.stderr)


def test_schedule_shutdown_builds_the_command(monkeypatch) -> None:
    if not is_supported():
        pytest.skip("Windows only")
    fake = FakeRun()
    monkeypatch.setattr(system_actions.subprocess, "run", fake)
    result = schedule_shutdown()
    assert result.ok and str(SHUTDOWN_DELAY_S) in result.message
    assert fake.calls[0][:4] == ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY_S)]
    assert "/c" in fake.calls[0]

    schedule_shutdown(0, comment="x" * 1000)
    assert fake.calls[1][3] == "0" and len(fake.calls[1][-1]) == 511


def test_cancel_shutdown(monkeypatch) -> None:
    if not is_supported():
        pytest.skip("Windows only")
    fake = FakeRun()
    monkeypatch.setattr(system_actions.subprocess, "run", fake)
    assert cancel_shutdown().ok
    assert fake.calls[0] == ["shutdown", "/a"]


def test_failures_are_reported_not_raised(monkeypatch) -> None:
    if not is_supported():
        pytest.skip("Windows only")
    monkeypatch.setattr(system_actions.subprocess, "run", FakeRun(returncode=1, stderr="Access is denied.\n"))
    result = schedule_shutdown()
    assert not result.ok and result.message == "Access is denied."

    monkeypatch.setattr(system_actions.subprocess, "run", FakeRun(exc=OSError("boom")))
    result = cancel_shutdown()
    assert not result.ok and "boom" in result.message

    monkeypatch.setattr(system_actions.subprocess, "run", FakeRun(exc=subprocess.TimeoutExpired("shutdown", 30)))
    assert not schedule_shutdown().ok


def test_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(system_actions.os, "name", "posix")
    assert not schedule_shutdown().ok and "Windows" in schedule_shutdown().message
    assert not cancel_shutdown().ok
