"""Authenticated endpoints for system status, log viewer, hotspot, .env guard, and allowlisted scripts."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.config import ROOT_DIR
from app.env_guard import is_env_protected, protect_env, unprotect_env
from app.errors import AppError
from app.logging_setup import clear_log_file
from app.security import require_auth
from app.state import AppState

logger = logging.getLogger("smart_home_av.system")
router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_auth)])

SCRIPT_CATALOG: list[dict] = [
    {
        "id": "check_status",
        "label": "Run Status Check (check_status.ps1)",
        "description": "Checks Scheduled Task, background PIDs, port 8000 reachability, and recent logs.",
        "remote": True,
    },
    {
        "id": "fix_firewall",
        "label": "Fix Windows Firewall (fix_firewall.ps1)",
        "description": "Adds inbound TCP port 8000 rule for Private & Public (Wi-Fi/Hotspot). Prompts UAC on PC if needed.",
        "remote": True,
    },
    {
        "id": "install_task",
        "label": "Register Startup Task (install_task.ps1)",
        "description": "Registers SmartHomeAVServer in Task Scheduler to start silently on Windows logon.",
        "remote": True,
    },
    {
        "id": "uninstall_task",
        "label": "Remove Startup Task (uninstall_task.ps1)",
        "description": "Removes SmartHomeAVServer from Task Scheduler and Startup folder.",
        "remote": True,
    },
    {
        "id": "hotspot_on",
        "label": "Turn PC Hotspot ON",
        "description": "Starts Windows Mobile Hotspot (or offline Wi-Fi Direct AP) using your configured hotspot password.",
        "remote": True,
    },
    {
        "id": "hotspot_off",
        "label": "Turn PC Hotspot OFF",
        "description": "Stops Windows Mobile Hotspot / offline Wi-Fi Direct AP.",
        "remote": True,
    },
    {
        "id": "env_hide",
        "label": "Hide & Lock .env",
        "description": "Sets Hidden + System + Read-Only attributes and owner-only NTFS permissions on .env.",
        "remote": True,
    },
    {
        "id": "env_unhide",
        "label": "Unhide .env",
        "description": "Clears Hidden/System/Read-Only attributes so you can view or edit .env locally.",
        "remote": True,
    },
    {
        "id": "send_email",
        "label": "Send Access URLs Email Now",
        "description": "Immediately sends your active Wi-Fi, Hotspot, and Cloudflare URLs to your Gmail (requires internet).",
        "remote": True,
    },
    {
        "id": "git_update",
        "label": "Update from Git (main) & Restart",
        "description": "Pulls latest code from origin/main, updates packages if needed, logs progress, and restarts the server.",
        "remote": True,
    },
]

PS_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "check_status": ("scripts/check_status.ps1",),
    "fix_firewall": ("scripts/fix_firewall.ps1", "-Elevate"),
    "install_task": ("scripts/install_task.ps1",),
    "uninstall_task": ("scripts/uninstall_task.ps1",),
}


class ScriptRunRequest(BaseModel):
    script_id: str = Field(min_length=1, max_length=40)


def _get_state(request: Request) -> AppState:
    return request.app.state.ctx


def _read_tail_lines(log_file: Path, count: int) -> list[str]:
    if not log_file.exists():
        return []
    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\r\n") for line in deque(fh, maxlen=count)]


def _query_windows_flags() -> dict:
    if sys.platform != "win32":
        return {"task_state": "non-windows", "firewall_active": False}
    ps_cmd = (
        "$t = Get-ScheduledTask -TaskName 'SmartHomeAVServer' -ErrorAction SilentlyContinue;"
        "$f = Get-NetFirewallRule -DisplayName 'Smart Home AV Server' -ErrorAction SilentlyContinue;"
        "Write-Output (\"$(if ($t) { $t.State } else { 'Missing' })|$(if ($f) { 'Active' } else { 'Missing' })\")"
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=8,
    )
    parts = (res.stdout.strip().split("|") + ["Unknown", "Unknown"])[:2]
    return {"task_state": parts[0], "firewall_active": parts[1] == "Active"}


def _run_allowlisted_ps(script_id: str, port: int) -> dict:
    rel_and_flags = PS_ALLOWLIST[script_id]
    script_path = (ROOT_DIR / rel_and_flags[0]).resolve()
    if not script_path.exists():
        raise AppError("SCRIPT_MISSING", f"Script not found: {rel_and_flags[0]}", 404)

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        *rel_and_flags[1:],
    ]
    if script_id in {"fix_firewall", "install_task"}:
        cmd.extend(["-Port", str(port)])

    res = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        "success": res.returncode == 0,
        "script_id": script_id,
        "exit_code": res.returncode,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


@router.get("/overview")
async def system_overview(request: Request) -> dict:
    ctx = _get_state(request)
    win_flags, hotspot_info = await asyncio.gather(
        asyncio.to_thread(_query_windows_flags),
        ctx.hotspot.refresh_status(),
    )
    return {
        "success": True,
        "pid": os.getpid(),
        "env_protected": is_env_protected(ROOT_DIR / ".env"),
        "hotspot": hotspot_info,
        **win_flags,
        "status": await asyncio.to_thread(ctx.build_status_payload),
    }


@router.get("/logs")
async def system_logs(
    request: Request,
    lines: int = Query(default=120, ge=10, le=500),
) -> dict:
    ctx = _get_state(request)
    log_path = ctx.settings.logs_dir / "server.log"
    entries = await asyncio.to_thread(_read_tail_lines, log_path, lines)
    return {"success": True, "lines": entries}


@router.post("/logs/clear")
async def clear_logs(request: Request) -> dict:
    ctx = _get_state(request)
    cleared = await asyncio.to_thread(clear_log_file, ctx.settings.logs_dir)
    return {"success": True, "cleared": cleared}


@router.get("/scripts")
async def list_scripts() -> dict:
    return {"success": True, "items": SCRIPT_CATALOG}


def _run_git_cmd(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=45,
    )


def _perform_git_update(root: Path = ROOT_DIR) -> dict:
    logs: list[str] = []
    remote = _run_git_cmd("remote", "get-url", "origin")
    if remote.returncode != 0:
        msg = "No Git remote 'origin' configured yet. Add one locally with: git remote add origin <repo-url>"
        logger.warning(msg)
        return {"success": False, "script_id": "git_update", "exit_code": 1, "stdout": msg, "stderr": "", "restarting": False}

    logger.info("Git update started from origin/main (%s)...", remote.stdout.strip())
    logs.append(f"origin: {remote.stdout.strip()}")

    pull = _run_git_cmd("pull", "--ff-only", "origin", "main")
    if pull.stdout.strip():
        logger.info("git pull: %s", pull.stdout.strip())
        logs.append(pull.stdout.strip())
    if pull.returncode != 0:
        err = pull.stderr.strip() or "git pull failed."
        logger.error("git pull failed: %s", err)
        return {"success": False, "script_id": "git_update", "exit_code": pull.returncode, "stdout": "\n".join(logs), "stderr": err, "restarting": False}

    pip_exe = root / ".venv" / "Scripts" / "pip.exe"
    if pip_exe.exists() and "Already up to date" not in pull.stdout:
        pip_res = subprocess.run([str(pip_exe), "install", "-r", "requirements.txt"], cwd=str(root), capture_output=True, text=True, timeout=120)
        logger.info("Updated Python dependencies (exit=%d).", pip_res.returncode)
        logs.append(f"pip install exit={pip_res.returncode}")

    restart_script = root / "scripts" / "restart_server.ps1"
    if restart_script.exists() and sys.platform == "win32":
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(restart_script), "-DelaySeconds", "2"],
            cwd=str(root),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        logger.info("Git update succeeded; restarting server in 2 seconds...")
        logs.append("Server restarting in 2 seconds...")

    return {"success": True, "script_id": "git_update", "exit_code": 0, "stdout": "\n".join(logs), "stderr": "", "restarting": True}


@router.post("/scripts/run")
async def run_script(body: ScriptRunRequest, request: Request) -> dict:
    ctx = _get_state(request)
    sid = body.script_id.strip()

    if sid == "hotspot_on":
        res = await ctx.hotspot.start_hotspot(manual=True)
        return {"success": True, "script_id": sid, "exit_code": 0, "stdout": str(res), "stderr": ""}
    if sid == "hotspot_off":
        res = await ctx.hotspot.stop_hotspot(manual=True)
        return {"success": True, "script_id": sid, "exit_code": 0, "stdout": str(res), "stderr": ""}
    if sid == "env_hide":
        ok = await asyncio.to_thread(protect_env, ROOT_DIR / ".env")
        return {"success": ok, "script_id": sid, "exit_code": 0 if ok else 1, "stdout": ".env hidden and locked (+H +S +R).", "stderr": ""}
    if sid == "env_unhide":
        ok = await asyncio.to_thread(unprotect_env, ROOT_DIR / ".env")
        return {"success": ok, "script_id": sid, "exit_code": 0 if ok else 1, "stdout": ".env is now visible and writable.", "stderr": ""}
    if sid == "send_email":
        ok = await ctx.notifier.send_now()
        msg = "Access URLs emailed via Gmail." if ok else "Email skipped (check internet connection or Gmail settings)."
        return {"success": ok, "script_id": sid, "exit_code": 0 if ok else 1, "stdout": msg, "stderr": ""}
    if sid == "git_update":
        return await asyncio.to_thread(_perform_git_update, ROOT_DIR)
    if sid not in PS_ALLOWLIST:
        raise AppError("INVALID_SCRIPT", f"Script '{sid}' is not allowlisted.", 400)

    return await asyncio.to_thread(_run_allowlisted_ps, sid, ctx.settings.app_port)
