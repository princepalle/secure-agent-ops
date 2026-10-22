"""Create .env from .env.example with freshly generated secrets. Refuses to overwrite an existing .env."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    target, template = ROOT / ".env", ROOT / ".env.example"
    if target.exists():
        print(".env already exists - leaving it untouched.")
        return 0
    generated = {
        "SAO_JWT_SECRET": secrets.token_urlsafe(48),
        "SAO_DEMO_PASSWORD": f"Demo-{secrets.token_urlsafe(9)}-7",
    }
    lines = []
    for line in template.read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={generated[key]}" if key in generated else line)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    print("Created .env with a random JWT secret and demo password (owner-readable only).")
    print("Demo password: see SAO_DEMO_PASSWORD in .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
