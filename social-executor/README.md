# Social Executor

本机 Hubstudio 辅助发布执行器。它连接已经由 Hubstudio 打开的 Chromium 调试端口，自动回填 X 或 Reddit 发布表单。`prepare` **永远不会点击最终发布按钮**；只有持有一次性确认令牌的 `confirm` 才会点击，并且必须识别真实帖子 URL 才返回 `published`。

## 安全边界

- 只监听 `127.0.0.1`，拒绝配置其他监听地址。
- 所有命令必须带 `X-Social-Executor-Secret`，共享密钥至少 32 字符。
- 只允许导航及验证 `https://x.com`、`https://twitter.com` 和 Reddit 域名。
- 每个 `container_code` 同一时间只运行一个命令。
- 不接收任意 JavaScript、选择器或任意目标网址。
- prepare 生成截图和结构化日志；日志会脱敏。
- 登录失效、验证码、DOM 变化、超时、结果 URL 无法验证时返回 `manual_required`。
- 确认令牌只保存在内存中，默认 30 分钟过期；服务重启后必须重新 prepare。

## 启动

需要 Node.js 20+。复制 `.env.example` 中的变量到进程环境，然后：

```powershell
npm install
npm start
```

执行器不会启动 Hubstudio 环境。后端需要先通过 Hubstudio Local API 打开指定环境，取得 `debuggingPort`，再调用执行器。

## 命令协议

`POST http://127.0.0.1:4317/v1/commands`

X prepare：

```json
{
  "command": "prepare",
  "job_id": "immutable-job-id",
  "container_code": "hubstudio-container-code",
  "debugging_port": 9222,
  "platform": "x",
  "content": { "body": "待发布内容", "url": "https://example.com/optional" }
}
```

Reddit prepare 的 `content` 必须使用 `title`、`body`、`subreddit`；`link_post` 可额外携带 `url`。执行器会回填标题、正文和目标 subreddit。成功返回 `awaiting_review`、截图文件名和 `confirmation_token`。后端应加密/短期保存该令牌。

确认发布：

```json
{
  "command": "confirm",
  "job_id": "immutable-job-id",
  "container_code": "hubstudio-container-code",
  "confirmation_token": "prepare-response-token"
}
```

只有确认动作会点击发布。返回 `published` 时一定包含经过域名 allowlist 检查的 `post_url`。

## 测试

```powershell
npm test
```

测试只覆盖 schema、URL allowlist、脱敏和 URL 构造，不连接真实浏览器或账号。
