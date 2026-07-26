from __future__ import annotations

import pytest

from app.clients.publishers import ImageUploadRequest, OpenAPIPublisher, PublishRequest, ShopifyPublisher, WordPressPublisher, markdown_to_gutenberg, strip_markdown_frontmatter
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
async def test_oemapps_article_connector_ignores_legacy_articles_defaults(monkeypatch):
    """Regression: the old form saved /articles, but OEMApps implements /posts."""
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called.update(method=method, url=url, headers=kwargs.get('headers'))
        return {'items': [{'id': 1, 'title': 'Hello'}]}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {
            'site_type': 'main',
            'api_base_url': 'https://openapi.oemapps.com',
            'api_config': {'tokenA': 'site-token', 'articlesPath': '/articles', 'publishPath': '/articles/save'},
        },
        dry_run=True,
    )

    result = await publisher.read_articles(limit=1)
    info = publisher.connection_info()

    assert result == [{'id': 1, 'title': 'Hello'}]
    assert called == {'method': 'GET', 'url': 'https://openapi.oemapps.com/posts', 'headers': {'token': 'site-token'}}
    assert info['config']['articles_path'] == '/posts'
    assert info['config']['publish_path'] == '/posts'


@pytest.mark.asyncio
async def test_oemapps_dry_run_uses_the_same_canonical_url_contract():
    publisher = OpenAPIPublisher(
        {
            'site_type': 'main',
            'base_url': 'https://avinoti.shop',
            'api_base_url': 'https://openapi.oemapps.com',
            'api_config': {'tokenB': 'site-token'},
        },
        dry_run=True,
    )

    result = await publisher.publish(
        PublishRequest(title='SEO Guide', slug='seo-guide', content_md='# Guide')
    )

    assert result.url == 'https://avinoti.shop/blogs/seo-guide'


@pytest.mark.asyncio
async def test_oemapps_sites_share_single_article_publish_adapter(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_request_json(method: str, url: str, **kwargs):
        calls.append({'method': method, 'url': url, 'headers': kwargs.get('headers'), 'json': kwargs.get('json')})
        return {'code': 0, 'msg': 'success', 'data': str(100 + len(calls))}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    request = PublishRequest(
        title='SEO Guide',
        slug='seo-guide',
        content_md='# SEO Guide\n\nUseful **content**.',
        meta_title='SEO Guide Title',
        meta_description='SEO guide description',
        primary_keyword='seo guide',
        author='Editorial Team',
        category_id='4275',
        image_cover_url='https://cdn.example.com/cover.jpg',
        status='publish',
    )

    sites = [
        ('tokenA', 'exdivo-token', 'https://exdivo.com', 'https://exdivo.com/blogs/seo-guide'),
        ('tokenB', 'avinoti-token', 'https://avinoti.shop', 'https://avinoti.shop/blogs/seo-guide'),
    ]
    for token_key, token_value, base_url, canonical_url in sites:
        publisher = OpenAPIPublisher(
            {
                'site_type': 'main',
                'base_url': base_url,
                'api_base_url': 'https://openapi.oemapps.com',
                'api_config': {
                    token_key: token_value,
                    'publishPath': '/posts/batch',
                    'articleUrlPath': '/blogs/detail/{id}',
                },
            },
            dry_run=False,
        )
        result = await publisher.publish(request)
        assert result.ok is True
        assert result.url == canonical_url

    assert [call['url'] for call in calls] == [
        'https://openapi.oemapps.com/posts',
        'https://openapi.oemapps.com/posts',
    ]
    assert [call['headers'] for call in calls] == [
        {'token': 'exdivo-token'},
        {'token': 'avinoti-token'},
    ]
    payload = calls[0]['json']
    assert payload == {
        'title': 'SEO Guide',
        'handle': 'seo-guide',
        'content': '<h1>SEO Guide</h1>\n\n<p>Useful <strong>content</strong>.</p>',
        'status': 1,
        'descript': 'SEO guide description',
        'meta_title': 'SEO Guide Title',
        'meta_descript': 'SEO guide description',
        'meta_keywords': ['seo guide'],
        'author_name': 'Editorial Team',
        'related_product_ids': [],
        'is_top': 0,
        'src': 'https://cdn.example.com/cover.jpg',
        'news_id': 4275,
    }


@pytest.mark.asyncio
async def test_oemapps_article_update_uses_site_token(monkeypatch):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called.update(method=method, url=url, headers=kwargs.get('headers'), json=kwargs.get('json'))
        return {'code': 0, 'msg': 'success', 'data': {'id': 7, 'handle': 'updated'}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {
            'site_type': 'main',
            'base_url': 'https://avinoti.shop',
            'api_base_url': 'https://openapi.oemapps.com',
            'api_config': {'tokenB': 'site-token', 'articleUrlPath': '/blogs/detail/{id}'},
        },
        dry_run=False,
    )

    result = await publisher.update('7', PublishRequest(title='Updated', slug='updated', content_md='# Updated', status='publish'))

    assert result.ok is True
    assert result.url == 'https://avinoti.shop/blogs/updated'
    assert called['method'] == 'PUT'
    assert called['url'] == 'https://openapi.oemapps.com/posts/7'
    assert called['headers'] == {'token': 'site-token'}
    assert called['json']['handle'] == 'updated'
    assert called['json']['status'] == 1
    assert called['json']['content'] == '<h1>Updated</h1>'


@pytest.mark.asyncio
@pytest.mark.parametrize(('token_key', 'token_value'), [('tokenA', 'exdivo-token'), ('tokenB', 'avinoti-token')])
async def test_oemapps_sites_share_image_upload_adapter(monkeypatch, token_key, token_value):
    called: dict[str, object] = {}

    async def fake_request_json(method: str, url: str, **kwargs):
        called.update(method=method, url=url, headers=kwargs.get('headers'), json=kwargs.get('json'))
        return {'code': 0, 'msg': 'success', 'data': {'id': 16756439, 'src': 'https://imgcdn.example.com/image.png'}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {'site_type': 'main', 'api_base_url': 'https://openapi.oemapps.com', 'api_config': {token_key: token_value}},
        dry_run=False,
    )

    result = await publisher.upload_image(ImageUploadRequest(type='url', url='https://source.example.com/image.png'))

    assert result.ok is True
    assert result.image_id == '16756439'
    assert result.src == 'https://imgcdn.example.com/image.png'
    assert called == {
        'method': 'POST',
        'url': 'https://openapi.oemapps.com/file/upload',
        'headers': {'token': token_value},
        'json': {'type': 'url', 'url': 'https://source.example.com/image.png'},
    }


@pytest.mark.asyncio
async def test_oemapps_image_upload_supports_base64_and_safe_dry_run(monkeypatch):
    called = False

    async def fake_request_json(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = OpenAPIPublisher(
        {'site_type': 'main', 'api_base_url': 'https://openapi.oemapps.com', 'api_config': {'tokenA': 'site-token'}},
        dry_run=True,
    )

    result = await publisher.upload_image(ImageUploadRequest(type='base64', base64='data:image/png;base64,AAAA'))

    assert result.ok is True
    assert result.dry_run is True
    assert result.raw == {'endpoint': '/file/upload', 'type': 'base64'}
    assert called is False


@pytest.mark.asyncio
async def test_oemapps_image_upload_rejects_invalid_input_before_network(monkeypatch):
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError('network must not be called')

    monkeypatch.setattr('app.clients.publishers.request_json', fail_if_called)
    publisher = OpenAPIPublisher(
        {'site_type': 'main', 'api_base_url': 'https://openapi.oemapps.com', 'api_config': {'tokenA': 'site-token'}},
        dry_run=False,
    )

    result = await publisher.upload_image(ImageUploadRequest(type='url', url='file:///tmp/image.png'))

    assert result.ok is False
    assert result.error == 'image upload url must use http:// or https://'


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
async def test_shopify_publisher_updates_and_reads_an_existing_article(monkeypatch):
    called: list[tuple[str, str, dict]] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})
    monkeypatch.setattr(
        'app.clients.publishers.get_settings',
        lambda: type('Settings', (), {'shopify_client_id': 'client-id', 'shopify_client_secret': 'client-secret', 'shopify_api_version': '2026-07'})(),
    )

    async def fake_request_json(method: str, url: str, **kwargs):
        called.append((method, url, kwargs))
        if url.endswith('/admin/oauth/access_token'):
            return {'access_token': 'shpat_test', 'expires_in': 3600, 'scope': 'read_content,write_content'}
        query = kwargs['json']['query']
        if 'mutation UpdateArticle' in query:
            assert kwargs['json']['variables']['id'] == 'gid://shopify/Article/1'
            assert kwargs['json']['variables']['article']['title'] == 'Updated'
            return {'data': {'articleUpdate': {'article': {'id': 'gid://shopify/Article/1', 'title': 'Updated', 'handle': 'hello'}, 'userErrors': []}}}
        return {'data': {'article': {'id': 'gid://shopify/Article/1', 'title': 'Updated', 'handle': 'hello', 'isPublished': True}}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher({'id': 'site-1', 'domain': 'example.myshopify.com', 'api_config': {'connector_type': 'shopify', 'blogHandle': 'news'}}, dry_run=False, credentials={'client_id': 'client-id', 'client_secret': 'client-secret'})

    result = await publisher.update('gid://shopify/Article/1', PublishRequest(
        title='Updated',
        slug='hello',
        content_md='# Body',
        meta_title='Updated search title',
        meta_description='Updated search description',
        status='publish',
    ))
    remote = await publisher.get_article('gid://shopify/Article/1')

    assert result.ok is True
    assert result.post_id == 'gid://shopify/Article/1'
    assert result.url == 'https://example.myshopify.com/blogs/news/hello'
    assert remote == {'id': 'gid://shopify/Article/1', 'title': 'Updated', 'handle': 'hello', 'isPublished': True, 'url': 'https://example.myshopify.com/blogs/news/hello'}
    assert 'mutation UpdateArticle' in called[1][2]['json']['query']
    assert called[1][2]['json']['variables']['article']['metafields'] == [
        {'namespace': 'global', 'key': 'title_tag', 'type': 'single_line_text_field', 'value': 'Updated search title'},
        {'namespace': 'global', 'key': 'description_tag', 'type': 'single_line_text_field', 'value': 'Updated search description'},
    ]


@pytest.mark.asyncio
async def test_shopify_publisher_syncs_only_seo_metafields(monkeypatch):
    called: list[tuple[str, str, dict]] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})
    monkeypatch.setattr(
        'app.clients.publishers.get_settings',
        lambda: type('Settings', (), {'shopify_client_id': 'client-id', 'shopify_client_secret': 'client-secret', 'shopify_api_version': '2026-07'})(),
    )

    async def fake_request_json(method: str, url: str, **kwargs):
        called.append((method, url, kwargs))
        if url.endswith('/admin/oauth/access_token'):
            return {'access_token': 'shpat_test', 'expires_in': 3600, 'scope': 'write_content'}
        return {'data': {'articleUpdate': {'article': {'id': 'gid://shopify/Article/1', 'title': 'Hello', 'handle': 'hello'}, 'userErrors': []}}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher({'id': 'site-1', 'domain': 'example.myshopify.com', 'api_config': {'connector_type': 'shopify', 'blogHandle': 'news'}}, dry_run=False, credentials={'client_id': 'client-id', 'client_secret': 'client-secret'})

    result = await publisher.sync_seo_metadata('gid://shopify/Article/1', PublishRequest(
        title='Hello', slug='hello', content_md='# This must not be sent', meta_title='Search title', meta_description='Search description', status='publish',
    ))

    assert result.ok is True
    request = called[1][2]['json']
    assert 'mutation UpdateArticleSeo' in request['query']
    assert request['variables'] == {
        'id': 'gid://shopify/Article/1',
        'article': {'metafields': [
            {'namespace': 'global', 'key': 'title_tag', 'type': 'single_line_text_field', 'value': 'Search title'},
            {'namespace': 'global', 'key': 'description_tag', 'type': 'single_line_text_field', 'value': 'Search description'},
        ]},
    }


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
    publisher = ShopifyPublisher({'id': 'site-1', 'domain': 'example.myshopify.com', 'api_config': {'connector_type': 'shopify', 'blogId': 'gid://shopify/Blog/1'}}, dry_run=False, credentials={'client_id': 'client-id', 'client_secret': 'client-secret'})

    result = await publisher.publish(PublishRequest(
        title='Hello',
        slug='hello',
        content_md='# Hello\n\n## Body',
        meta_title='Search title',
        meta_description='Search description',
        status='publish',
    ))

    assert result.ok is True
    assert result.post_id == 'gid://shopify/Article/1'
    assert called[0][1] == 'https://example.myshopify.com/admin/oauth/access_token'
    assert called[1][1] == 'https://example.myshopify.com/admin/api/2026-07/graphql.json'
    assert called[1][2]['headers']['X-Shopify-Access-Token'] == 'shpat_test'
    assert called[1][2]['json']['variables']['article']['blogId'] == 'gid://shopify/Blog/1'
    body = called[1][2]['json']['variables']['article']['body']
    assert '<h1>' not in body
    assert '<h2>Body</h2>' in body
    assert '>Hello<' not in body
    assert called[1][2]['json']['variables']['article']['metafields'] == [
        {'namespace': 'global', 'key': 'title_tag', 'type': 'single_line_text_field', 'value': 'Search title'},
        {'namespace': 'global', 'key': 'description_tag', 'type': 'single_line_text_field', 'value': 'Search description'},
    ]


@pytest.mark.asyncio
async def test_shopify_publisher_resolves_the_configured_blog_handle_before_creating(monkeypatch):
    called: list[tuple[str, str, dict]] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})
    monkeypatch.setattr(
        'app.clients.publishers.get_settings',
        lambda: type('Settings', (), {'shopify_client_id': 'client-id', 'shopify_client_secret': 'client-secret', 'shopify_api_version': '2026-07'})(),
    )

    async def fake_request_json(method: str, url: str, **kwargs):
        called.append((method, url, kwargs))
        if url.endswith('/admin/oauth/access_token'):
            return {'access_token': 'shpat_test', 'expires_in': 3600, 'scope': 'read_content,write_content'}
        query = kwargs['json']['query']
        if 'query Blogs' in query:
            return {'data': {'blogs': {'nodes': [{'id': 'gid://shopify/Blog/9', 'handle': 'guides'}]}}}
        return {'data': {'articleCreate': {'article': {'id': 'gid://shopify/Article/1', 'handle': 'hello'}, 'userErrors': []}}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher({'id': 'site-1', 'domain': 'example.myshopify.com', 'api_config': {'connector_type': 'shopify', 'blogHandle': 'guides'}}, dry_run=False, credentials={'client_id': 'client-id', 'client_secret': 'client-secret'})

    result = await publisher.publish(PublishRequest(title='Hello', slug='hello', content_md='Body', status='publish'))

    assert result.ok is True
    assert 'query Blogs' in called[1][2]['json']['query']
    assert called[2][2]['json']['variables']['article']['blogId'] == 'gid://shopify/Blog/9'


@pytest.mark.asyncio
async def test_shopify_tokens_are_isolated_by_site_and_credentials(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})

    async def fake_request_json(method: str, url: str, **kwargs):
        client_id = kwargs['data']['client_id']
        calls.append((url, client_id))
        return {'access_token': f'token-{client_id}', 'expires_in': 3600, 'scope': 'read_content,write_content'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    first = ShopifyPublisher(
        {'id': 'site-a', 'domain': 'first.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'client-a', 'client_secret': 'secret-a'},
    )
    second = ShopifyPublisher(
        {'id': 'site-b', 'domain': 'second.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'client-b', 'client_secret': 'secret-b'},
    )

    assert (await first._access_token('first.myshopify.com'))[0] == 'token-client-a'
    assert (await second._access_token('second.myshopify.com'))[0] == 'token-client-b'
    assert (await first._access_token('first.myshopify.com'))[0] == 'token-client-a'
    assert calls == [
        ('https://first.myshopify.com/admin/oauth/access_token', 'client-a'),
        ('https://second.myshopify.com/admin/oauth/access_token', 'client-b'),
    ]


@pytest.mark.asyncio
async def test_shopify_credential_rotation_cannot_reuse_old_token(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})

    async def fake_request_json(method: str, url: str, **kwargs):
        client_id = kwargs['data']['client_id']
        calls.append(client_id)
        return {'access_token': f'token-{client_id}', 'expires_in': 3600, 'scope': 'read_content,write_content'}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    old = ShopifyPublisher(
        {'id': 'site-a', 'domain': 'first.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'old-client', 'client_secret': 'old-secret'},
    )
    rotated = ShopifyPublisher(
        {'id': 'site-a', 'domain': 'first.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'new-client', 'client_secret': 'new-secret'},
    )

    assert (await old._access_token('first.myshopify.com'))[0] == 'token-old-client'
    assert (await rotated._access_token('first.myshopify.com'))[0] == 'token-new-client'
    assert calls == ['old-client', 'new-client']


@pytest.mark.asyncio
async def test_shopify_product_seo_mutation_is_strictly_allowlisted_and_not_retried(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})

    async def fake_request_json(method: str, url: str, **kwargs):
        if url.endswith('/admin/oauth/access_token'):
            return {'access_token': 'token', 'expires_in': 3600, 'scope': 'write_products'}
        calls.append(kwargs)
        query = kwargs['json']['query']
        if 'query ProductForSeo' in query:
            if sum('query ProductForSeo' in call['json']['query'] for call in calls) > 1:
                return {'data': {'product': {
                    'id': 'gid://shopify/Product/123',
                    'title': 'Dress',
                    'handle': 'dress',
                    'updatedAt': '2026-07-24T00:01:00Z',
                    'seo': {'title': 'SEO Dress', 'description': 'SEO description'},
                }}}
            return {'data': {'product': {
                'id': 'gid://shopify/Product/123',
                'title': 'Dress',
                'handle': 'dress',
                'updatedAt': '2026-07-24T00:00:00.000Z',
                'seo': {'title': '', 'description': ''},
            }}}
        return {'data': {'productUpdate': {'product': {
            'id': 'gid://shopify/Product/123',
            'title': 'Dress',
            'handle': 'dress',
            'updatedAt': '2026-07-24T00:01:00Z',
            'seo': {'title': 'SEO Dress', 'description': 'SEO description'},
        }, 'userErrors': []}}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher(
        {'id': 'site-a', 'domain': 'shop.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'client', 'client_secret': 'secret'},
    )

    updated = await publisher.update_product_seo(
        'gid://shopify/Product/123',
        title='SEO Dress',
        description='SEO description',
        expected_updated_at='2026-07-24T00:00:00+00:00',
    )

    mutation = calls[1]
    assert mutation['json']['variables'] == {
        'product': {
            'id': 'gid://shopify/Product/123',
            'seo': {'title': 'SEO Dress', 'description': 'SEO description'},
        }
    }
    assert mutation['max_attempts'] == 1
    assert len(calls) == 3
    forbidden = {'variants', 'price', 'compareAtPrice', 'inventoryQuantity', 'sku', 'handle', 'status', 'descriptionHtml'}
    assert not (forbidden & set(mutation['json']['variables']['product']))
    assert updated['seo']['title'] == 'SEO Dress'


@pytest.mark.asyncio
async def test_shopify_product_seo_rejects_stale_snapshot_before_mutation(monkeypatch):
    queries: list[str] = []
    monkeypatch.setattr('app.clients.publishers._SHOPIFY_TOKEN_CACHE', {})

    async def fake_request_json(method: str, url: str, **kwargs):
        if url.endswith('/admin/oauth/access_token'):
            return {'access_token': 'token', 'expires_in': 3600, 'scope': 'write_products'}
        queries.append(kwargs['json']['query'])
        return {'data': {'product': {
            'id': 'gid://shopify/Product/123',
            'updatedAt': '2026-07-24T00:02:00Z',
            'seo': {'title': 'Changed elsewhere', 'description': ''},
        }}}

    monkeypatch.setattr('app.clients.publishers.request_json', fake_request_json)
    publisher = ShopifyPublisher(
        {'id': 'site-a', 'domain': 'shop.myshopify.com'},
        dry_run=False,
        credentials={'client_id': 'client', 'client_secret': 'secret'},
    )

    with pytest.raises(ExternalCallError, match='changed after review'):
        await publisher.update_product_seo(
            'gid://shopify/Product/123',
            title='SEO Dress',
            description='SEO description',
            expected_updated_at='2026-07-24T00:00:00Z',
        )

    assert len(queries) == 1
    assert 'mutation UpdateProductSeo' not in queries[0]


def test_shopify_connector_is_selected_for_shopify_site():
    from app.clients.publishers import connector_for_site

    connector = connector_for_site({'site_type': 'shopify', 'domain': 'example.myshopify.com'})

    assert isinstance(connector, ShopifyPublisher)
