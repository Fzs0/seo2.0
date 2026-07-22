"""Authenticated local client for the Node social executor."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx


class SocialExecutorError(RuntimeError):
    pass


class SocialExecutorClient:
    def __init__(self, *, base_url: str, shared_secret: str, client: httpx.AsyncClient | None = None) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 4317:
            raise SocialExecutorError("social executor must use http://127.0.0.1:4317")
        if len(shared_secret) < 32:
            raise SocialExecutorError("SOCIAL_EXECUTOR_SHARED_SECRET must be at least 32 characters")
        self._url = base_url.rstrip("/") + "/v1/commands"
        self._secret = shared_secret
        self._client = client

    async def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=60, follow_redirects=False)
        close = self._client is None
        try:
            response = await client.post(
                self._url, json=payload, headers={"X-Social-Executor-Secret": self._secret},
            )
            if response.status_code != 200:
                raise SocialExecutorError(f"social executor returned HTTP {response.status_code}")
            result = response.json()
            if not isinstance(result, dict) or not result.get("status"):
                raise SocialExecutorError("social executor returned an invalid response")
            return result
        except SocialExecutorError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise SocialExecutorError(f"social executor call failed: {type(error).__name__}") from error
        finally:
            if close:
                await client.aclose()


__all__ = ["SocialExecutorClient", "SocialExecutorError"]
