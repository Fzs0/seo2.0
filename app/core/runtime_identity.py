"""Runtime/source identity used to detect a stale local backend process."""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parent


def source_fingerprint(app_root: Path = APP_ROOT) -> str:
    digest = hashlib.sha256()
    for path in sorted(app_root.rglob("*.py"), key=lambda item: item.as_posix()):
        relative = path.relative_to(app_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def capture_runtime_identity() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "revision": _git_revision(),
        "startup_source_fingerprint": source_fingerprint(),
    }


def runtime_health(identity: dict[str, Any]) -> dict[str, Any]:
    current = source_fingerprint()
    startup = str(identity["startup_source_fingerprint"])
    return {
        **identity,
        "current_source_fingerprint": current,
        "source_drift": current != startup,
    }


def _git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


__all__ = ["capture_runtime_identity", "runtime_health", "source_fingerprint"]
