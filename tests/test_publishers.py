from __future__ import annotations

import pytest

from app.clients.publishers import OpenAPIPublisher, PublishRequest, ShopifyPublisher, WordPressPublisher, markdown_to_gutenberg, strip_markdown_frontmatter
from app.clients.http_client import ExternalCallError


def test_markdown_to_gutenberg_uses_separate_blocks():
    content = markdown_to_gutenberg("# Title\n\nParagraph **bold**.\n\n- One\n- Two")

    assert '<!-- wp:heading {"level":1} -->' in content
    assert '<!-- wp:paragraph -->' in content
    assert '<!-- wp:list -->' in content
    assert '<strong>bold</strong>' in content
    assert content.count("<!-- wp:") == 3


def test_strip_markdown_frontmatter_keeps_body_and_metadata():
    body, metadata = strip_markdown_frontmatter('---\ntitle: "SEO title"\nmeta_description: Description\n---\n\n# Body')

    assert body == '# Body'
    assert metadata == {'title': 'SEO title', 'meta_description': 'Description'}


def test_strip_fenced_or_bare_yaml_metadata_keeps_only_body():
    fenced_body, fenced_metadata = strip_markdown_frontmatter(
        '```yaml\ntitle: "SEO title"\nmeta_description: "Description"\n```\n\n# Body\n\nParagraph'
    )
    bare_body, bare_metadata = strip_markdown_frontmatter(
        'title: "SEO title"\nmeta_description: "Description"\n\n# Body\n\nParagraph'
    )

    assert fenced_body == bare_body == '# Body\n\nParagraph'
    assert fenced_metadata == bare_metadata == {'title': 'SEO title', 'meta_description': 'Description'}


@pytest.mark.parametrize('key', ['Meta Description', 'metaDescription', 'meta-description'])
def test_strip_markdown_frontmatter_normalizes_meta_description_key(key: str) -> None:
    body, metadata = strip_markdown_frontmatter(f'---\n{key}: A useful description\n---\n\n# Body')

    assert body == '# Body'
    assert metadata['meta_description'] == 'A useful description'


def test_strip_wrapped_markdown_and_horizontal_rules():
    body, metadata = strip_markdown_frontmatter(
        '```markdown\n---\ntitle: "SEO title"\n---\n\n# Body\n\nIntro\n\n---\n\n## Next\n\nText\n```'
    )

    assert body == '# Body\n\nIntro\n\n## Next\n\nText'
    assert metadata == {'title': 'SEO title'}
    assert '```' not in body
    assert '---' not in body


def test_markdown_to_gutenberg_ignores_horizontal_rules():
    content = markdown_to_gutenberg('# Body\n\nIntro\n\n---\n\n## Next')

    assert 'wp:separator' not in content
    assert '<hr' not in content


def test_strip_editorial_tail_and_wordpress_soft_breaks() -> None:
    body, _ = strip_markdown_frontmatter(
        "# Body\n\nFirst line\nSecond line\n\n*(Only include references when safety claims are made.)*\n\nPost-Publish Notes (Editorial Only – Not for Readers)\nReview this later."
    )

    assert body == "# Body\n\nFirst line\nSecond line"
    assert "<br />" not in markdown_to_gutenberg(body)


def test_strip_markdown_metadata_template_keeps_only_article_body():
    body, metadata = strip_markdown_frontmatter(
        '## Title\nSEO title\n\n## Meta Description\nDescription\n\n# Body\n\nArticle text'
    )

    assert body == '# Body\n\nArticle text'
    assert metadata['title'] == 'SEO title'
    assert metadata['meta_description'] == 'Description'


def test_strip_bold_metadata_lines() -> None:
    body, metadata = strip_markdown_frontmatter(
        '# Body\n\n**Meta Title:** SEO title\n\n**Meta Description:** Description\n\nArticle text'
    )

    assert body == '# Body\n\nArticle text'
    assert metadata == {'meta_title': 'SEO title', 'meta_description': 'Description'}


@pytest.mark.asyncio
async def test_openapi_publisher_posts_batch_json(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called['method'] = method
        called['url'] = url
        called['headers'] = kwargs.get('headers')
        called['json'] = kwargs.get('json')
        return {'created': [{'id': '42', 'slug': 'hello'}], 'failed': []}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)

    publisher = OpenAPIPublisher(
        {
            'site_type': 'blog',
            'api_base_url': 'https://api.example.com',
            'domain': 'api.example.com',
            'api_config': {'openApiKey': 'key-1', 'defaultAuthor': 'seo'},
        },
        dry_run=False,
    )

    result = await publisher.publish(
        PublishRequest(title='Hello', slug='hello', content_md='---\ntitle: Other\n---\n\n# Body', status='publish')
    )

    assert result.ok is True
    assert called['method'] == 'POST'
    assert called['url'] == 'https://api.example.com/posts/batch'
    assert called['headers'] == {'X-API-Key': 'key-1', 'Host': 'api.example.com'}
    assert called['json']['items'][0]['slug'] == 'hello'
    assert called['json']['items'][0]['status'] == 'published'
    assert called['json']['items'][0]['content_md'] == '# Body'


@pytest.mark.asyncio
async def test_openapi_publisher_reads_posts_with_documented_protocol(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called['method'] = method
        called['url'] = url
        called['headers'] = kwargs.get('headers')
        called['params'] = kwargs.get('params')
        return {'items': [{'id': 1, 'title': 'Hello'}], 'page': 1, 'page_size': 1, 'total': 1}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {'domain': 'api.example.com', 'api_base_url': 'https://api.example.com/api/open/v1', 'api_config': {'openApiKey': 'key-1'}},
        dry_run=True,
    )

    result = await publisher.read_articles(limit=1)

    assert result == [{'id': 1, 'title': 'Hello'}]
    assert called['method'] == 'GET'
    assert called['url'] == 'https://api.example.com/api/open/v1/posts'
    assert called['headers'] == {'X-API-Key': 'key-1', 'Host': 'api.example.com'}
    assert called['params'] == {'page': 1, 'page_size': 1}


@pytest.mark.asyncio
async def test_openapi_publisher_gets_one_post_and_finds_slug(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def fake_request_json(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/posts/42'):
            return {'code': 0, 'data': {'id': 42, 'slug': 'hello', 'title': 'Hello', 'status': 'published'}}
        return {'items': [{'id': 42, 'slug': 'hello'}]}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {'domain': 'api.example.com', 'api_base_url': 'https://api.example.com/api/open/v1', 'api_config': {'openApiKey': 'key-1'}},
        dry_run=False,
    )

    item = await publisher.get_article('42')
    found = await publisher.find_article_by_slug('HELLO')

    assert item and item['id'] == 42
    assert found and found['id'] == 42
    assert calls[0][1] == 'https://api.example.com/api/open/v1/posts/42'


@pytest.mark.asyncio
async def test_openapi_get_article_falls_back_to_bounded_list(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def fake_request_json(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/posts/42'):
            raise ExternalCallError('connector_openapi_article 状态码 404')
        return {'items': [
            {'id': 41, 'title': 'Other', 'status': 'published'},
            {'id': 42, 'slug': 'hello', 'title': 'Hello', 'status': 'published'},
        ]}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {'domain': 'api.example.com', 'api_base_url': 'https://api.example.com/api/open/v1', 'api_config': {'openApiKey': 'key-1'}},
        dry_run=False,
    )

    item = await publisher.get_article('42')

    assert item == {'id': 42, 'slug': 'hello', 'title': 'Hello', 'status': 'published'}
    assert len(calls) == 2
    assert calls[1][1] == 'https://api.example.com/api/open/v1/posts'
    assert calls[1][2]['params'] == {'page': 1, 'page_size': 100}


@pytest.mark.asyncio
async def test_openapi_publisher_updates_one_post(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called.update(method=method, url=url, headers=kwargs.get('headers'), json=kwargs.get('json'))
        return {'id': 2, 'slug': 'summer-vape-guide', 'status': 'published', 'title': 'Updated'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {
            'site_type': 'blog',
            'base_url': 'https://example.com',
            'api_base_url': 'https://example.com/api/open/v1',
            'api_config': {'openApiKey': 'key-1', 'articleUrlPath': '/blog/{slug}'},
        },
        dry_run=False,
    )

    result = await publisher.update('summer vape/guide', PublishRequest(title='Updated', slug='ignored-new-slug', content_md='# Updated', status='publish'))

    assert result.ok is True
    assert result.post_id == '2'
    assert result.url == 'https://example.com/blog/summer-vape-guide'
    assert called['method'] == 'PUT'
    assert called['url'] == 'https://example.com/api/open/v1/posts/summer%20vape%2Fguide'
    assert called['headers'] == {'Authorization': 'Bearer key-1'}
    assert called['json'] == {
        'title': 'Updated',
        'content_md': '# Updated',
        'format': 'markdown',
        'status': 'published',
    }


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
        PublishRequest(
            title='Hello',
            slug='hello',
            content_md='```yaml\ntitle: Other\nmeta_description: Other description\n```\n\nIntro before heading\n\n# Body\n\nParagraph',
            meta_title='SEO title',
            meta_description='SEO description',
            primary_keyword='hello keyword',
            status='publish',
        )
    )

    assert result.ok is True
    assert called['method'] == 'POST'
    assert called['url'] == 'https://site.example.com/wp-json/wp/v2/posts'
    assert called['json']['slug'] == 'hello'
    assert called['json']['status'] == 'publish'
    assert '<!-- wp:paragraph -->' in called['json']['content']
    assert '<h1>Body</h1>' not in called['json']['content']
    assert 'title: Other' not in called['json']['content']
    assert 'meta_description:' not in called['json']['content']
    assert '<p>Paragraph</p>' in called['json']['content']
    assert called['json']['meta'] == {
        'rank_math_title': 'SEO title',
        'rank_math_description': 'SEO description',
        'rank_math_focus_keyword': 'hello keyword',
    }
    assert str(called['headers']['Authorization']).startswith('Basic ')


@pytest.mark.asyncio
async def test_wordpress_publisher_updates_existing_post(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called.update(method=method, url=url, headers=kwargs.get('headers'), json=kwargs.get('json'))
        return {'id': 1837, 'link': 'https://site.example.com/existing'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = WordPressPublisher(
        {'domain': 'https://site.example.com', 'api_config': {'username': 'admin', 'applicationPassword': 'app password'}},
        dry_run=False,
    )

    result = await publisher.update('1837', PublishRequest(title='Updated', slug='new-slug', content_md='# New body', status='publish'))

    assert result.ok is True
    assert called['method'] == 'POST'
    assert called['url'] == 'https://site.example.com/wp-json/wp/v2/posts/1837'
    assert called['json']['title'] == 'Updated'
    assert 'slug' not in called['json']


@pytest.mark.asyncio
async def test_wordpress_publisher_gets_id_and_finds_slug(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def fake_request_json(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/1837'):
            return {'id': 1837, 'slug': 'existing'}
        return [{'id': 1837, 'slug': 'existing'}]

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = WordPressPublisher(
        {'domain': 'https://site.example.com', 'api_config': {'username': 'admin', 'applicationPassword': 'app password'}},
        dry_run=False,
    )

    item = await publisher.get_article('1837')
    found = await publisher.find_article_by_slug('existing')

    assert item and item['id'] == 1837
    assert found and found['id'] == 1837
    assert calls[0][2]['params'] == {'context': 'edit'}
    assert calls[1][2]['params']['slug'] == 'existing'


@pytest.mark.asyncio
async def test_shopify_update_remains_explicitly_unsupported():
    publisher = ShopifyPublisher({'domain': 'example.myshopify.com'}, dry_run=False)

    result = await publisher.update('gid://shopify/Article/1', PublishRequest(title='Hello', slug='hello', content_md='# Body'))

    assert result.ok is False
    assert result.error == 'connector shopify does not support update_article'


@pytest.mark.asyncio
async def test_shopify_publisher_uses_client_credentials_and_publishes(monkeypatch):
    called: list[tuple[str, str, dict]] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})
    monkeypatch.setattr(
        'app.clients.publishers.get_settings',
        lambda: type('Settings', (), {'shopify_client_id': 'client-id', 'shopify_client_secret': 'client-secret', 'shopify_api_version': '2026-07'})(),
    )

    async def fake_request_json(method: str, url: str, **kwargs):
        called.append((method, url, kwargs))
        if url.endswith('/admin/oauth/access_token'):
            assert kwargs['data']['grant_type'] == 'client_credentials'
            return {'access_token': 'shpat_test', 'expires_in': 3600, 'scope': 'read_content,write_content'}
        return {'data': {'articleCreate': {'article': {'id': 'gid://shopify/Article/1', 'handle': 'hello'}, 'userErrors': []}}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher({'domain': 'example.myshopify.com', 'api_config': {'connector_type': 'shopify', 'blogId': 'gid://shopify/Blog/1'}}, dry_run=False)

    result = await publisher.publish(PublishRequest(title='Hello', slug='hello', content_md='# Body', status='publish'))

    assert result.ok is True
    assert result.post_id == 'gid://shopify/Article/1'
    assert called[0][1] == 'https://example.myshopify.com/admin/oauth/access_token'
    assert called[1][1] == 'https://example.myshopify.com/admin/api/2026-07/graphql.json'
    assert called[1][2]['headers']['X-Shopify-Access-Token'] == 'shpat_test'
    assert called[1][2]['json']['variables']['article']['blogId'] == 'gid://shopify/Blog/1'


def test_shopify_connector_is_selected_for_shopify_site():
    from app.clients.publishers import connector_for_site

    connector = connector_for_site({'site_type': 'shopify', 'domain': 'example.myshopify.com'})

    assert isinstance(connector, ShopifyPublisher)
