from __future__ import annotations

import pytest

from app.services import site_media_service


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _Session:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.params: dict | None = None

    async def execute(self, statement, params: dict) -> _Rows:
        assert "business_id = :business_id" in str(statement)
        assert "is_main IS TRUE" in str(statement)
        self.params = params
        return _Rows(self.rows)


class _Publisher:
    capabilities = ("upload_image",)


@pytest.mark.asyncio
async def test_custom_blog_media_routes_to_same_business_oemapps_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exdivo_main = {
        "id": "exdivo-main",
        "business_id": "exdivo",
        "site_key": "exdivo",
        "site_type": "main",
        "base_url": "https://exdivo.com",
        "api_base_url": "https://openapi.oemapps.com",
        "status": "active",
        "api_config": {"tokenA": "configured"},
    }
    session = _Session([exdivo_main])
    publisher = _Publisher()
    captured: dict = {}

    async def runtime(_session, site, **kwargs):
        captured.update(site=site, kwargs=kwargs)
        return publisher

    monkeypatch.setattr(site_media_service, "publisher_for_site_runtime", runtime)

    resolution = await site_media_service.resolve_site_media_uploader(
        session,  # type: ignore[arg-type]
        {
            "id": "topvapes",
            "business_id": "exdivo",
            "site_key": "topvapes.de",
            "site_type": "blog",
            "base_url": "https://topvapes.de",
            "api_base_url": "https://topvapes.de/api/open/v1",
            "status": "active",
            "api_config": {"openApiKey": "configured"},
        },
        dry_run=False,
        require_active=True,
    )

    assert session.params == {"business_id": "exdivo"}
    assert resolution.publisher is publisher
    assert resolution.media_host_site["id"] == "exdivo-main"
    assert resolution.transport == "business_oemapps_upload_then_article_publish"
    assert captured["site"]["business_id"] == "exdivo"
    assert captured["kwargs"] == {"dry_run": False, "require_active": True}


@pytest.mark.asyncio
async def test_custom_blog_media_rejects_missing_same_business_oemapps_main() -> None:
    session = _Session([])

    with pytest.raises(ValueError, match="same-business OEMApps media host"):
        await site_media_service.resolve_site_media_uploader(
            session,  # type: ignore[arg-type]
            {
                "id": "topvapes",
                "business_id": "exdivo",
                "site_key": "topvapes.de",
                "site_type": "blog",
                "base_url": "https://topvapes.de",
                "api_base_url": "https://topvapes.de/api/open/v1",
                "status": "active",
                "api_config": {"openApiKey": "configured"},
            },
            dry_run=True,
            require_active=False,
        )


@pytest.mark.asyncio
async def test_wordpress_media_stays_on_the_target_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _Publisher()
    captured: dict = {}

    async def runtime(_session, site, **kwargs):
        captured.update(site=site, kwargs=kwargs)
        return publisher

    monkeypatch.setattr(site_media_service, "publisher_for_site_runtime", runtime)
    site = {
        "id": "vapes2000",
        "business_id": "exdivo",
        "site_key": "vapes2000",
        "site_type": "wp",
        "base_url": "https://vapes2000.com",
        "status": "active",
        "api_config": {},
    }

    resolution = await site_media_service.resolve_site_media_uploader(
        object(),  # type: ignore[arg-type]
        site,
        dry_run=True,
        require_active=False,
    )

    assert resolution.publisher is publisher
    assert resolution.media_host_site is site
    assert resolution.transport == "direct_upload"
    assert captured["site"] is site
