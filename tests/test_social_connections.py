from __future__ import annotations

import httpx
import pytest

from app.connectors.social_connections import HubstudioConnection, SocialConnectionError, XApiConnection


@pytest.mark.asyncio
async def test_x_connection_reads_real_account_shape_without_leaking_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.x.com"
        assert request.headers["authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"data": {"id": "42", "username": "exdivo", "name": "ExDivo"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await XApiConnection(access_token="secret-token", client=client).test()

    assert result["account"]["username"] == "exdivo"
    assert "secret-token" not in str(result)


@pytest.mark.asyncio
async def test_x_connection_rejects_authentication_failure_without_response_body() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "secret-token invalid"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SocialConnectionError, match="HTTP 401") as error:
            await XApiConnection(access_token="secret-token", client=client).test()
    assert "secret-token" not in str(error.value)


@pytest.mark.asyncio
async def test_hubstudio_logs_in_then_reads_all_first_page_environments() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/login":
            assert b'"appId":"app"' in request.content
            return httpx.Response(200, json={"msg": "Success", "code": 0, "data": None})
        assert request.url.path == "/api/v1/env/list"
        assert b'"size":200' in request.content
        return httpx.Response(200, json={
            "msg": "Success", "code": 0,
            "data": {"total": 1, "list": [{"containerCode": 2669, "containerName": "Reddit", "tagName": "Social"}]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HubstudioConnection(
            app_id="app", app_secret="secret", group_code="group", client=client,
        ).test()

    assert calls == ["/login", "/api/v1/env/list"]
    assert result["environments"][0]["container_code"] == "2669"
    assert "secret" not in str(result)


def test_hubstudio_rejects_partial_credentials_and_nonlocal_targets() -> None:
    with pytest.raises(SocialConnectionError, match="provided together"):
        HubstudioConnection(app_id="app")
    with pytest.raises(SocialConnectionError, match="127.0.0.1"):
        HubstudioConnection(base_url="http://192.168.1.5:6873")


@pytest.mark.asyncio
async def test_hubstudio_open_requires_matching_container_and_debugging_port() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/browser/start"
        assert b'"containerCode":"2669"' in request.content
        return httpx.Response(200, json={
            "msg": "Success", "code": 0,
            "data": {"containerCode": "2669", "debuggingPort": "59591", "browserID": 42},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HubstudioConnection(client=client).open_environment("2669")

    assert result == {"container_code": "2669", "debugging_port": "59591", "browser_id": "42"}
