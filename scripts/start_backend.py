"""Start the local FastAPI backend without opening a console window."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with (
        (LOG_DIR / "backend.out.log").open("a", encoding="utf-8") as stdout,
        (LOG_DIR / "backend.err.log").open("a", encoding="utf-8") as stderr,
    ):
        process = subprocess.Popen(
            [
                str(ROOT / ".venv" / "Scripts" / "python.exe"),
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(ROOT),
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    print(f"backend started pid={process.pid}")


if __name__ == "__main__":
    main()
