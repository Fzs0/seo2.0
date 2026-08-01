# AI On-page 统一 Action 验收报告

日期：2026-07-30
范围：后端 AI 正式链路、平台 Adapter、能力契约、生命周期、测试与文档
不在范围：前端交互改造、无审批自动执行、真实回滚、生产远端写入

## 验收结论

目标链路已经形成：

```text
自主研究
→ Run-local on_page_fix
→ 数据库目标身份回查
→ Formal Plan 具体 Action
→ 精确平台 Adapter
→ Preview
→ Approve
→ 单次 Execute
→ Independent Readback
→ 页面级 Observation
→ Strategy Run 收口
```

本轮没有恢复旧 workflow On-page 旁路，没有修改前端，没有新增 migration，
没有连接生产数据库，也没有调用真实业务站远端写接口。

## 平台矩阵

| 平台 | 首页 | 产品 | 分类 | 当前结果 |
| --- | --- | --- | --- | --- |
| OEMApps | `homepage_seo` | `product_seo`、`product_image_alt` | `category_seo` | 已注册统一 Adapter |
| Shopify | Hold | `product_seo` | Hold | 仅产品已注册 |
| WordPress | 不适用 | 不适用 | 不适用 | 博客 TDK 继续 `update_article` |
| Custom OpenAPI | Hold | Hold | Hold | 当前连接器契约只读，未声明安全写入与独立回读 |

## 安全验收

- `on_page_fix` 不直接执行；Formal Plan 转为具体 Action。
- Run-local 不依赖 candidate 或 keyword。
- 本地资产 ID或稳定远端 ID会回查现有数据库，并校验业务、站点、URL、远端对象和连接器。
- Router 只接受精确平台注册组合；未知组合返回 `action_adapter_not_configured`，远端调用为 0。
- 能力快照哈希包含字段、读写、独立回读、副作用和 Adapter 标识/版本。
- 审批绑定 capability、before、patch、target 和 Adapter 身份。
- OEMApps Variant、分类成员置顶和首页写入确认缺失时，在远端写入前阻断。
- Shopify 只允许 Meta Title 和 Meta Description。
- 执行按 Action 幂等，且同站点写入使用数据库 advisory lock 与持久状态双重门禁。
- 写后独立回读；不一致只创建一个 P1，不创建正向 Observation，不自动重写。
- 超时恢复只读；确认未写入后回到 `planned`，清除旧审批并要求重新 Preview。
- 页面级观察包含 T+0、7、14、28、56、90 天，保存页面类型、URL、前后哈希及 GSC/GA4 baseline。

## 实际验证

```text
后端全量：
.\.venv\Scripts\python.exe -m pytest -q
621 passed, 25 skipped

PG17 隔离发布门禁：
.\scripts\test-pg17-release-gate.ps1 -Dsn <disposable-pg17-dsn>
PostgreSQL 17.10
23 passed

平台与生命周期组合回归：
109 passed

前端生产构建：
npm.cmd run build
TypeScript + Vite passed

Python compileall：
passed
```

PG17 门禁覆盖 OEMApps 与 Shopify `product_seo` 的
Run-local → Formal Plan → concrete Action → Preview → Approve → fake/no-network
Execute → independent readback → Observation → parent Run completed，并覆盖审批后能力
变化、回读不一致只生成一个 P1 且不创建正向观察、缺失精确 Adapter 时禁止创建
Action。测试数据库名称明确包含 `test`，测试 Adapter 不发网络请求。

## 保留限制

- 本机回环、`env=local`、源码无漂移且由用户显式调用时，可继续使用完整 Action
  审批链；全局身份认证和 business scope 授权仍是共享或对外部署门禁。
- `/start` 仍是同步、可恢复入口，不是独立常驻队列 Worker。
- Custom OpenAPI 没有写入和独立回读契约，因此默认 Hold。
- Shopify 首页和分类没有已验证写入能力，因此默认 Hold。
- 真实回滚和无审批自动执行仍禁止。
