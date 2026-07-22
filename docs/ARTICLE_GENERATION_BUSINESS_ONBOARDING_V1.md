# 新业务接入文章生成清单 V1

目标：未知业务资料不足时必须转人工，不能沿用旧行业规则自动生文。

## 1. 先确认站点知识画像

站点的 `knowledge_profile.status` 必须为 `confirmed`，并至少有：

- `positioning`：这个站点为什么能回答该主题；
- `audience`：服务谁；
- `in_scope_topics`：可以持续覆盖的主题；
- `generation_policy`：见下一节。

内容站还必须在每次文章任务里提供明确 `user_question`，并有 SERP 快照或允许来源。缺一项即转人工。

## 2. generation_policy 模板

```json
{
  "site_role": "editorial",
  "business_type": "B2B SaaS",
  "risk_level": "low",
  "keyword_triggers": {
    "finance": ["mortgage", "loan"]
  },
  "claim_terms": ["interest rate", "tax advice"],
  "allowed_sources": [
    {
      "url": "https://example.gov/official-guidance",
      "label": "Official guidance",
      "source_type": "official"
    }
  ],
  "forbidden_claims": ["Do not promise an investment return."],
  "required_modules": [],
  "metadata_length": {"min": 120, "max": 160}
}
```

- `site_role`：`commercial` / `local_service` 需要已确认承接页和事实；`editorial` 不强制 CTA。
- `risk_level`：健康、金融、法律、受监管商品使用 `regulated` 或 `ymyl`。
- 高风险主题命中 `keyword_triggers` 后，没有 `allowed_sources` 会在调用 AI 前拦截。
- 中文、日文、韩文可用 `metadata_length: {"min": 50, "max": 160}`。

## 3. 明日的安全测试方式

使用 `POST /api/v1/workflow/article-test`，而不是策略执行。它不会保存文章、修改关键词、创建任务或效果观察数据。

每个新业务至少测试四篇：

1. 低风险教程；
2. 对比或商业调研文；
3. 本业务的高风险主题（有允许来源）；
4. 同一高风险主题（移除来源，应被拦截）。

发布前再检查页面本身：可被 Google 抓取、无 `noindex`、返回 200、canonical 正确、已进入 sitemap；这些是发布层检查，不由正文生成器替代。
