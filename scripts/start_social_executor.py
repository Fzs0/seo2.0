"""Start the local social executor with the same secret as the backend.

This launcher intentionally passes the secret through the child environment
without printing it or persisting it in process arguments/log files.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    secret = settings.social_executor_shared_secret or settings.connector_secret_key
    if len(secret) < 32:
        raise RuntimeError("Social executor shared secret is not configured")

    LOG_DIR.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["SOCIAL_EXECUTOR_SHARED_SECRET"] = secret
    with (
        (LOG_DIR / "social-executor.out.log").open("a", encoding="utf-8") as stdout,
        (LOG_DIR / "social-executor.err.log").open("a", encoding="utf-8") as stderr,
    ):
        process = subprocess.Popen(
            ["node", "src/server.js"],
            cwd=ROOT / "social-executor",
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    print(f"social-executor started pid={process.pid}")


if __name__ == "__main__":
    main()
