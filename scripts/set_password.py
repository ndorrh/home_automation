"""CLI helper to change dashboard password and re-wrap the recording private key."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import ensure_env_file
from app.crypto import generate_keypair_bundle, rewrap_private_key
from app.env_guard import write_env_safely
from app.security import hash_password, verify_password


def _read_env_map(env_path: Path) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def _write_updates(env_path: Path, updates: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    write_env_safely(env_path, "\n".join(out) + "\n")


def main() -> None:
    env_path = ensure_env_file(ROOT_DIR)
    env_map = _read_env_map(env_path)
    old_hash = env_map.get("APP_PASSWORD_HASH", "")
    old_wrapped = env_map.get("ENC_PRIVATE_KEY_WRAPPED", "")

    old_pwd = ""
    if old_hash and old_wrapped:
        old_pwd = getpass.getpass("Current dashboard password: ")
        if not verify_password(old_pwd, old_hash):
            print("Error: current password is incorrect.")
            sys.exit(1)

    new_pwd = getpass.getpass("Enter new dashboard password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if not new_pwd or new_pwd != confirm:
        print("Error: new passwords did not match or were empty.")
        sys.exit(1)

    updates = {"APP_PASSWORD_HASH": hash_password(new_pwd)}
    if old_wrapped:
        updates["ENC_PRIVATE_KEY_WRAPPED"] = rewrap_private_key(old_pwd, new_pwd, old_wrapped)
    else:
        pub, wrapped = generate_keypair_bundle(new_pwd)
        updates["ENC_PUBLIC_KEY"] = pub
        updates["ENC_PRIVATE_KEY_WRAPPED"] = wrapped

    _write_updates(env_path, updates)
    print(f"Updated password hash and encryption key wrapper in {env_path}")


if __name__ == "__main__":
    main()
