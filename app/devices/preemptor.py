"""Manual-only release of desktop applications holding webcam or microphone."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

logger = logging.getLogger("smart_home_av.preemptor")

PROTECTED_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "system",
        "system idle process",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "winlogon.exe",
        "dwm.exe",
        "explorer.exe",
        "taskhostw.exe",
        "sihost.exe",
        "audiodg.exe",
        "conhost.exe",
        "runtimebroker.exe",
        "searchhost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "ctfmon.exe",
        "python.exe",
        "pythonw.exe",
        "cloudflared.exe",
        "node.exe",
    }
)

KNOWN_AV_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "windowscamera.exe",
        "camera.exe",
        "zoom.exe",
        "teams.exe",
        "ms-teams.exe",
        "skype.exe",
        "discord.exe",
        "obs64.exe",
        "obs32.exe",
        "webex.exe",
        "slack.exe",
    }
)

CONSENT_PATHS: tuple[str, ...] = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam",
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone",
)


@dataclass(frozen=True)
class ReleaseReport:
    """Result of a manual device-release action."""

    terminated: list[str]
    skipped: list[str]
    failed: list[str]


def _read_active_consent_names() -> set[str]:
    """Read executable basenames currently marked active in Windows ConsentStore."""
    if sys.platform != "win32":
        return set()

    import winreg  # Windows-only stdlib module

    active: set[str] = set()
    for reg_path in CONSENT_PATHS:
        _collect_subkeys(winreg, reg_path, active)
    return active


def _collect_subkeys(winreg, reg_path: str, out: set[str]) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            _scan_key_entries(winreg, key, out, is_non_packaged=False)
    except OSError:
        return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{reg_path}\\NonPackaged") as key:
            _scan_key_entries(winreg, key, out, is_non_packaged=True)
    except OSError:
        return


def _scan_key_entries(winreg, parent_key, out: set[str], is_non_packaged: bool) -> None:
    idx = 0
    while True:
        try:
            sub_name = winreg.EnumKey(parent_key, idx)
            idx += 1
        except OSError:
            break
        if sub_name == "NonPackaged":
            continue
        _check_single_subkey(winreg, parent_key, sub_name, out, is_non_packaged)


def _check_single_subkey(
    winreg, parent_key, sub_name: str, out: set[str], is_non_packaged: bool
) -> None:
    try:
        with winreg.OpenKey(parent_key, sub_name) as sub:
            stop_time, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
            if stop_time != 0:
                return
    except OSError:
        return

    if is_non_packaged:
        exe = Path(sub_name.replace("#", "\\")).name.lower()
        if exe.endswith(".exe"):
            out.add(exe)
        return

    pkg_token = sub_name.split("_")[0].lower()
    if "camera" in pkg_token:
        out.add("windowscamera.exe")


def _protected_pids() -> set[int]:
    pids = {os.getpid(), os.getppid()}
    try:
        for parent in psutil.Process(os.getpid()).parents():
            pids.add(parent.pid)
    except psutil.Error:
        pass
    return pids


def release_conflicting_av_processes(
    active_names: set[str] | None = None,
    processes: list[psutil.Process] | None = None,
) -> ReleaseReport:
    """Terminate user processes holding the camera or microphone (manual trigger only)."""
    targets = set(KNOWN_AV_PROCESS_NAMES)
    targets.update(active_names if active_names is not None else _read_active_consent_names())

    protected = _protected_pids()
    proc_list = processes if processes is not None else list(psutil.process_iter(["pid", "name"]))

    terminated: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    for proc in proc_list:
        _handle_candidate_process(proc, targets, protected, terminated, skipped, failed)

    logger.info(
        "Manual device release finished: terminated=%s skipped=%d failed=%s",
        terminated,
        len(skipped),
        failed,
    )
    return ReleaseReport(terminated=terminated, skipped=skipped, failed=failed)


def _handle_candidate_process(
    proc: psutil.Process,
    targets: set[str],
    protected: set[int],
    terminated: list[str],
    skipped: list[str],
    failed: list[str],
) -> None:
    try:
        name = (proc.info.get("name") if hasattr(proc, "info") else proc.name()) or ""
        pid = proc.pid
    except psutil.Error:
        return

    lower_name = name.lower()
    if lower_name not in targets:
        return

    if pid in protected or lower_name in PROTECTED_PROCESS_NAMES:
        skipped.append(f"{name}({pid})")
        return

    try:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except psutil.TimeoutExpired:
            proc.kill()
        terminated.append(f"{name}({pid})")
    except psutil.Error as exc:
        failed.append(f"{name}({pid}): {exc}")
