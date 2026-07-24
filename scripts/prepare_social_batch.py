"""Prepare social publish jobs sequentially without clicking final publish.

Each positional job uses ``environment:platform:job_uuid``. Progress is written
atomically to a JSON state file so the UI operator can monitor a long batch
without holding an HTTP connection open.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_job(value: str) -> dict[str, str]:
    environment, platform, job_id = value.split(":", 2)
    UUID(job_id)
    if not environment.isdigit() or not platform.replace("_", "").isalnum():
        raise ValueError(f"invalid job descriptor: {value}")
    return {"environment": environment, "platform": platform, "job_id": job_id}


def _prepare(base_url: str, business_id: str, job_id: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/api/v1/social/publish-jobs/{job_id}/prepare",
        data=json.dumps({"business_id": business_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=950) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {
            "ok": False,
            "status": "failed",
            "error": error.read().decode("utf-8", errors="replace")[:1000],
            "http_status": error.code,
        }
    except Exception as error:  # noqa: BLE001 - operator state must capture failures
        return {
            "ok": False,
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", nargs="+", type=_parse_job)
    parser.add_argument("--business-id", default="exdivo")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("logs/social-prepare-batch.json"),
    )
    args = parser.parse_args()

    state: dict[str, Any] = {
        "status": "running",
        "business_id": args.business_id,
        "started_at": _now(),
        "updated_at": _now(),
        "total": len(args.jobs),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "current": None,
        "items": [],
    }
    _write_state(args.state_file, state)

    for job in args.jobs:
        state["current"] = job
        state["updated_at"] = _now()
        _write_state(args.state_file, state)
        started = time.monotonic()
        result = _prepare(
            args.base_url.rstrip("/"),
            args.business_id,
            job["job_id"],
        )
        item = {
            **job,
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "error": result.get("error"),
            "reason_code": result.get("reason_code"),
            "duration_seconds": round(time.monotonic() - started, 1),
        }
        state["items"].append(item)
        state["completed"] += 1
        state["succeeded" if item["ok"] else "failed"] += 1
        state["updated_at"] = _now()
        _write_state(args.state_file, state)

    state["status"] = "completed"
    state["current"] = None
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    _write_state(args.state_file, state)


if __name__ == "__main__":
    main()
