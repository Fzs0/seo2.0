"""Real connection-test adapters for X and local Hubstudio."""
from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


class SocialConnectionError(RuntimeError):
    """A credential-free connection test error safe to show to operators."""


class SocialConnectionAdapter(Protocol):
    async def test(self) -> dict[str, Any]: ...


class XApiConnection:
    def __init__(self, *, access_token: str, client: httpx.AsyncClient | None = None) -> None:
        if not access_token:
            raise SocialConnectionError("X user access_token is required")
        self._access_token = access_token
        self._client = client

    async def test(self) -> dict[str, Any]:
        return await self._request(
            "https://api.x.com/2/users/me",
            params={"user.fields": "created_at,description,verified,public_metrics,profile_image_url"},
        )

    async def _request(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        close = self._client is None
        try:
            response = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"},
            )
            if response.status_code in {401, 403}:
                raise SocialConnectionError(f"X user authentication failed with HTTP {response.status_code}")
            if response.status_code != 200:
                raise SocialConnectionError(f"X connection test returned HTTP {response.status_code}")
            payload = response.json()
            account = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(account, dict) or not account.get("id") or not account.get("username"):
                raise SocialConnectionError("X /2/users/me response is missing account identity")
            return {
                "platform": "x", "capabilities": ["account", "publisher"],
                "account": {
                    "id": str(account["id"]), "username": str(account["username"]),
                    "name": str(account.get("name") or ""), "verified": bool(account.get("verified")),
                    "protected": bool(account.get("protected")),
                },
            }
        except SocialConnectionError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise SocialConnectionError(f"X connection test failed: {type(error).__name__}") from error
        finally:
            if close:
                await client.aclose()


class HubstudioConnection:
    def __init__(
        self, *, base_url: str = "http://127.0.0.1:6873", app_id: str = "",
        app_secret: str = "", group_code: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 6873:
            raise SocialConnectionError("Hubstudio Local API must use http://127.0.0.1:6873")
        self._base_url = base_url.rstrip("/")
        supplied = [bool(app_id), bool(app_secret), bool(group_code)]
        if any(supplied) and not all(supplied):
            raise SocialConnectionError("Hubstudio app_id, app_secret and group_code must be provided together")
        self._app_id = app_id
        self._app_secret = app_secret
        self._group_code = group_code
        self._client = client

    async def test(self) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(
            timeout=10, follow_redirects=False, trust_env=False,
        )
        close = self._client is None
        try:
            await self._login_if_configured(client)
            environments: list[Any] = []
            total = 0
            page = 1
            while page <= 50:
                response = await client.post(
                    f"{self._base_url}/api/v1/env/list",
                    json={"current": page, "size": 200},
                    headers={"Accept-Language": "zh-CN", "Content-Type": "application/json"},
                )
                if response.status_code != 200:
                    raise SocialConnectionError(f"Hubstudio connection test returned HTTP {response.status_code}")
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("code") != 0:
                    raise SocialConnectionError("Hubstudio returned an unsuccessful response")
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                items = data.get("list") if isinstance(data.get("list"), list) else []
                try:
                    total = max(total, int(data.get("total") or 0))
                except (TypeError, ValueError):
                    raise SocialConnectionError("Hubstudio environment total is invalid") from None
                environments.extend(items)
                if not items or len(environments) >= total or len(items) < 200:
                    break
                page += 1
            if total > len(environments):
                raise SocialConnectionError("Hubstudio environment pagination exceeded the 10,000 item safety limit")
            unique: dict[str, dict[str, str]] = {}
            clean = [
                {
                    "container_code": str(item.get("containerCode") or ""),
                    "container_name": str(item.get("containerName") or ""),
                    "group_name": str(item.get("tagName") or ""),
                    "group_code": str(item.get("tagCode") or ""),
                }
                for item in environments if isinstance(item, dict) and item.get("containerCode")
            ]
            for item in clean:
                unique[item["container_code"]] = item
            clean = list(unique.values())
            return {
                "platform": "reddit", "capabilities": ["environment_list", "browser_open"],
                "account": {"environment_count": len(clean)}, "environments": clean,
            }
        except SocialConnectionError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise SocialConnectionError(f"Hubstudio connection test failed: {type(error).__name__}") from error
        finally:
            if close:
                await client.aclose()

    async def open_environment(self, container_code: str) -> dict[str, str]:
        if not str(container_code).strip():
            raise SocialConnectionError("Hubstudio container_code is required")
        client = self._client or httpx.AsyncClient(
            timeout=30, follow_redirects=False, trust_env=False,
        )
        close = self._client is None
        try:
            await self._login_if_configured(client)
            response = await client.post(
                f"{self._base_url}/api/v1/browser/start",
                json={
                    "containerCode": str(container_code), "isHeadless": False,
                    "shouldCloseTabsOnOpen": "false",
                },
                headers={"Content-Type": "application/json"},
            )
            if response.status_code != 200:
                raise SocialConnectionError(f"Hubstudio browser start returned HTTP {response.status_code}")
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
            if payload.get("code") != 0 or not data.get("debuggingPort"):
                raise SocialConnectionError("Hubstudio browser start did not return debuggingPort")
            returned_code = str(data.get("containerCode") or container_code)
            if returned_code != str(container_code):
                raise SocialConnectionError("Hubstudio opened a different container")
            return {
                "container_code": returned_code,
                "debugging_port": str(data["debuggingPort"]),
                "browser_id": str(data.get("browserID") or data.get("containerId") or ""),
            }
        except SocialConnectionError:
            raise
        except (httpx.HTTPError, ValueError) as error:
            raise SocialConnectionError(f"Hubstudio browser start failed: {type(error).__name__}") from error
        finally:
            if close:
                await client.aclose()

    async def _login_if_configured(self, client: httpx.AsyncClient) -> None:
        if not self._app_id:
            return
        login = await client.post(
            f"{self._base_url}/login",
            json={"appId": self._app_id, "appSecret": self._app_secret, "groupCode": self._group_code},
            headers={"Content-Type": "application/json"},
        )
        if login.status_code != 200:
            raise SocialConnectionError(f"Hubstudio login returned HTTP {login.status_code}")
        payload = login.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise SocialConnectionError("Hubstudio login failed")


def connection_adapter(platform: str, config: dict[str, Any], secrets: dict[str, str]) -> SocialConnectionAdapter:
    if platform in {"x", "reddit", "quora", "tiktok", "youtube", "instagram", "facebook"}:
        return HubstudioConnection(
            base_url=str(config.get("base_url") or "http://127.0.0.1:6873"),
            app_id=secrets.get("app_id", ""), app_secret=secrets.get("app_secret", ""),
            group_code=secrets.get("group_code", ""),
        )
    raise SocialConnectionError(f"unsupported social connection platform: {platform}")


__all__ = [
    "HubstudioConnection", "SocialConnectionAdapter", "SocialConnectionError",
    "XApiConnection", "connection_adapter",
]
