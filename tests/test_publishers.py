from __future__ import annotations

import pytest

from app.clients.publishers import OpenAPIPublisher, PublishRequest, WordPressPublisher


@pytest.mark.asyncio
async def test_openapi_publisher_posts_json(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called['method'] = method
        called['url'] = url
        called['headers'] = kwargs.get('headers')
        called['json'] = kwargs.get('json')
        return {'id': '42', 'url': 'https://api.example.com/articles/hello'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)

    publisher = OpenAPIPublisher(
        {
            'site_type': 'blog',
            'api_base_url': 'https://api.example.com',
            'api_config': {'openApiKey': 'key-1', 'defaultAuthor': 'seo'},
        },
        dry_run=False,
    )

    result = await publisher.publish(
        PublishRequest(title='Hello', slug='hello', content_md='Body', status='publish')
    )

    assert result.ok is True
    assert called['method'] == 'POST'
    assert called['url'] == 'https://api.example.com/articles/save'
    assert called['headers'] == {'openApiKey': 'key-1'}
    assert called['json']['slug'] == 'hello'
    assert called['json']['status'] == 'publish'


@pytest.mark.asyncio
async def test_wordpress_publisher_posts_json(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called['method'] = method
        called['url'] = url
        called['headers'] = kwargs.get('headers')
        called['json'] = kwargs.get('json')
        return {'id': 99, 'link': 'https://site.example.com/hello'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)

    publisher = WordPressPublisher(
        {
            'site_type': 'wp',
            'domain': 'https://site.example.com',
            'api_config': {'username': 'admin', 'applicationPassword': 'app password'},
        },
        dry_run=False,
    )

    result = await publisher.publish(
        PublishRequest(title='Hello', slug='hello', content_md='Body', status='publish')
    )

    assert result.ok is True
    assert called['method'] == 'POST'
    assert called['url'] == 'https://site.example.com/wp-json/wp/v2/posts'
    assert called['json']['slug'] == 'hello'
    assert called['json']['status'] == 'publish'
    assert str(called['headers']['Authorization']).startswith('Basic ')
