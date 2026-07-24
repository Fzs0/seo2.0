"""Start the restricted relay and a Cloudflare quick tunnel for the extension."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE = LOG_DIR / "extension-tunnel-state.json"
TUNNEL_LOG = LOG_DIR / "extension-tunnel.err.log"
CONFIG = ROOT / "hubstudio-social-extension" / "tunnel-config.js"
CLOUDFLARED = Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe")
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def running(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return str(pid) in result.stdout


def reuse_existing() -> bool:
    if not STATE.exists():
        return False
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if not running(int(state["relay_pid"])) or not running(int(state["tunnel_pid"])):
        return False
    url = str(state["url"])
    CONFIG.write_text(
        f'globalThis.EXDIVO_TUNNEL_BACKEND = "{url}";\n',
        encoding="utf-8",
    )
    print(json.dumps(state))
    return True


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    if reuse_existing():
        return
    with (
        (LOG_DIR / "extension-relay.out.log").open("a", encoding="utf-8") as relay_out,
        (LOG_DIR / "extension-relay.err.log").open("a", encoding="utf-8") as relay_err,
    ):
        relay = subprocess.Popen(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "scripts/extension_tunnel_relay.py")],
            cwd=ROOT,
            stdout=relay_out,
            stderr=relay_err,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    TUNNEL_LOG.write_text("", encoding="utf-8")
    with (
        (LOG_DIR / "extension-tunnel.out.log").open("a", encoding="utf-8") as tunnel_out,
        TUNNEL_LOG.open("a", encoding="utf-8") as tunnel_err,
    ):
        tunnel = subprocess.Popen(
            [
                str(CLOUDFLARED),
                "tunnel",
                "--url",
                "http://127.0.0.1:8765",
                "--no-autoupdate",
            ],
            cwd=ROOT,
            stdout=tunnel_out,
            stderr=tunnel_err,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    deadline = time.monotonic() + 45
    url = ""
    while time.monotonic() < deadline:
        text = TUNNEL_LOG.read_text(encoding="utf-8", errors="replace")
        match = URL_PATTERN.search(text)
        if match:
            url = match.group(0)
            break
        if tunnel.poll() is not None:
            raise RuntimeError("cloudflared exited before returning a tunnel URL")
        time.sleep(0.25)
    if not url:
        tunnel.terminate()
        relay.terminate()
        raise RuntimeError("cloudflared did not return a tunnel URL within 45 seconds")
    state = {"url": url, "relay_pid": relay.pid, "tunnel_pid": tunnel.pid}
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    CONFIG.write_text(
        f'globalThis.EXDIVO_TUNNEL_BACKEND = "{url}";\n',
        encoding="utf-8",
    )
    print(json.dumps(state))


if __name__ == "__main__":
    main()
