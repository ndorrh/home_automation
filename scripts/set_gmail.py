"""CLI helper to seal Gmail credentials into .env using Windows DPAPI."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.bootstrap import ensure_env_file
from app.env_guard import write_env_safely
from app.vault import GmailCredentials, seal_gmail_credentials


def update_gmail_sealed(env_path: Path, token: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in {"GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL_TO"}:
            continue
        if key == "GMAIL_CREDENTIALS_ENC":
            out.append(f"GMAIL_CREDENTIALS_ENC={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"GMAIL_CREDENTIALS_ENC={token}")
    write_env_safely(env_path, "\n".join(out) + "\n")


def main() -> None:
    user = input("Gmail address (sender): ").strip()
    app_pw = getpass.getpass("16-character Gmail App Password: ").strip()
    to_addr = input(f"Recipient email [{user}]: ").strip() or user
    if not user or not app_pw:
        print("Error: Gmail address and App Password are required.")
        sys.exit(1)

    sealed = seal_gmail_credentials(
        GmailCredentials(user=user, app_password=app_pw, notify_to=to_addr)
    )
    env_path = ensure_env_file(ROOT_DIR)
    update_gmail_sealed(env_path, sealed)
    print(f"Encrypted Gmail credentials saved to GMAIL_CREDENTIALS_ENC in {env_path}")


if __name__ == "__main__":
    main()
