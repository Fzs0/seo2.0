"""Restricted HTTP relay for the Hubstudio extension Cloudflare tunnel."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BACKEND = "http://127.0.0.1:8000"
HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 1024 * 1024
STATIC_ROUTES = {
    ("GET", "/api/health"),
    ("POST", "/api/v1/social/extension/pair"),
    ("POST", "/api/v1/social/extension/heartbeat"),
    ("GET", "/api/v1/social/extension/tasks/next"),
}
STAGE_ROUTE = re.compile(
    r"^/api/v1/social/extension/tasks/"
    r"[0-9a-fA-F-]{36}/stage$"
)


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "ExdivoExtensionRelay/1.0"

    def do_GET(self) -> None:  # noqa: N802
        self._relay()

    def do_POST(self) -> None:  # noqa: N802
        self._relay()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._relay()

    def log_message(self, format: str, *args: object) -> None:
        # Do not persist request headers, tokens, or payloads.
        print(f"{self.command} {self.path.split('?', 1)[0]} {args[1] if len(args) > 1 else ''}")

    def _allowed(self) -> bool:
        method = (
            self.headers.get("Access-Control-Request-Method", "GET")
            if self.command == "OPTIONS"
            else self.command
        )
        path = self.path.split("?", 1)[0]
        return (method, path) in STATIC_ROUTES or (
            method == "POST" and STAGE_ROUTE.fullmatch(path) is not None
        )

    def _relay(self) -> None:
        if not self._allowed():
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else None
        headers = {}
        for name in (
            "Authorization",
            "Content-Type",
            "Origin",
            "Access-Control-Request-Method",
            "Access-Control-Request-Headers",
        ):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        request = urllib.request.Request(
            BACKEND + self.path,
            data=body,
            method=self.command,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                self.send_response(response.status)
                self._copy_response_headers(response.headers)
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self._copy_response_headers(error.headers)
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, urllib.error.URLError):
            self.send_error(502)

    def _copy_response_headers(self, headers: object) -> None:
        for name in (
            "Content-Type",
            "Access-Control-Allow-Origin",
            "Access-Control-Allow-Methods",
            "Access-Control-Allow-Headers",
            "Vary",
        ):
            value = headers.get(name)
            if value:
                self.send_header(name, value)
        self.send_header("Cache-Control", "no-store")


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), RelayHandler).serve_forever()
