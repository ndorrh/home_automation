"""Unit tests for manual-only AV device release logic."""

from __future__ import annotations

import os

from app.devices.preemptor import release_conflicting_av_processes


class FakeProc:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self.info = {"pid": pid, "name": name}
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float = 2.0) -> None:
        return None

    def kill(self) -> None:
        self.killed = True


def test_release_skips_system_and_self_processes() -> None:
    self_proc = FakeProc(os.getpid(), "python.exe")
    system_proc = FakeProc(100, "svchost.exe")
    explorer_proc = FakeProc(101, "explorer.exe")
    cam_proc = FakeProc(202, "WindowsCamera.exe")
    custom_active_proc = FakeProc(203, "SomeRecorder.exe")

    report = release_conflicting_av_processes(
        active_names={"somerecorder.exe"},
        processes=[self_proc, system_proc, explorer_proc, cam_proc, custom_active_proc],
    )

    assert self_proc.terminated is False
    assert system_proc.terminated is False
    assert explorer_proc.terminated is False
    assert cam_proc.terminated is True
    assert custom_active_proc.terminated is True
    assert "WindowsCamera.exe(202)" in report.terminated
    assert "SomeRecorder.exe(203)" in report.terminated
