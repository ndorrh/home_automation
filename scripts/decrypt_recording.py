"""Local CLI helper to decrypt a .enc recording after verifying the dashboard password."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.crypto import decrypt_file_iter, unwrap_private_key
from app.media.recordings import decrypted_download_name


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/decrypt_recording.py <path-to-file.enc> [output-path]")
        sys.exit(1)

    enc_path = Path(sys.argv[1]).resolve()
    if not enc_path.exists():
        print(f"File not found: {enc_path}")
        sys.exit(1)

    out_path = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) >= 3
        else enc_path.with_name(decrypted_download_name(enc_path.name))
    )

    cfg = get_settings()
    pwd = getpass.getpass("Enter dashboard password: ")
    priv_raw = unwrap_private_key(pwd, cfg.enc_private_key_wrapped)

    with out_path.open("wb") as fout:
        for chunk in decrypt_file_iter(enc_path, priv_raw):
            fout.write(chunk)

    print(f"Decrypted file written to: {out_path}")


if __name__ == "__main__":
    main()
