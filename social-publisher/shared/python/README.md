# Exdivo Social Contract

主项目与独立 Social Publisher 共用的纯 Python 平台契约 Module。

它是以下规则的单一事实来源：

- 七个平台的 capability、发布 Adapter、默认 URL 与域名 allowlist；
- 内容包和账号绑定校验；
- Hubstudio 连接配置与密钥字段校验。

包不依赖 FastAPI、SQLAlchemy、HTTP 客户端或任一 `app` 包。两个后端保留原
`app.services.social_platform_registry` import 路径作为兼容 facade。

```bash
python -m pip install -e .
python -m pytest -q
```
