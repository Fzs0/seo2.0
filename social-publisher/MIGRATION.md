# 从当前 Windows 项目迁移到 Mac

## 1. 导出社媒数据

在 Windows 上安装 PostgreSQL 客户端后执行：

```powershell
pg_dump `
  --dbname "postgresql://seo:seo_dev_local@127.0.0.1:5433/seo_workbench" `
  --schema social `
  --data-only `
  --format custom `
  --file social-data.dump
```

只导出 `social` schema，不需要迁移 SEO、关键词、文章和知识库数据。

## 2. 复制文件

复制到 Mac：

- `social-publisher/`
- `social-data.dump`
- `social-video/` 中仍要发布的视频。

不要复制 `node_modules`、`.venv`、日志和浏览器缓存。

## 3. 初始化 Mac

```bash
cd social-publisher
bash scripts/setup-macos.sh
```

## 4. 恢复数据

```bash
pg_restore \
  --dbname "postgresql://publisher:publisher@127.0.0.1:55432/social_publisher" \
  --data-only \
  --disable-triggers \
  social-data.dump
```

如果 Mac 没有 `pg_restore`，可以使用 Docker 容器：

```bash
docker compose cp social-data.dump postgres:/tmp/social-data.dump
docker compose exec postgres pg_restore \
  --username publisher \
  --dbname social_publisher \
  --data-only \
  --disable-triggers \
  /tmp/social-data.dump
```

## 5. 处理加密凭证

已有 `social.connection_secrets` 使用 Windows 项目中的 `CONNECTOR_SECRET_KEY` 加密。

二选一：

1. 将 Windows `.env` 中原来的 `CONNECTOR_SECRET_KEY` 安全写入 Mac 的 `.env`；
2. 不迁移 `connection_secrets`，在 Mac 的 `/docs` 重新创建 Hubstudio 连接。

不要通过聊天、邮件或 Git 传输该密钥。

## 6. 改写视频路径

```bash
.venv/bin/python scripts/rewrite_media_paths.py \
  'C:\Users\PC\Desktop\seo2.0\social-video' \
  '/Users/alice/Exdivo/social-video'
```

## 7. 验证

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:4317/health
curl "http://127.0.0.1:8000/api/v1/social/publish-jobs?business_id=exdivo&limit=10"
curl -X POST http://127.0.0.1:6873/api/v1/env/list \
  -H "Content-Type: application/json" \
  -d '{}'
```

四个检查都成功后，再进行一次单账号、单平台的回填测试。
