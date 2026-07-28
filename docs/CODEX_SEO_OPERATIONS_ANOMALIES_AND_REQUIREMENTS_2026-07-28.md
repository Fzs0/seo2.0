# Codex SEO 运营异常与后端需求清单

日期：2026-07-28
依据：2026-07-25 至 2026-07-27 的真实策略运行、三主站发布回读、当前源码和项目文档。
目标：让 Codex 可以只通过统一 API 安全完成“取证 → 决策 → 审批 → 执行 → 回读 → 观察”，不再依靠临时数据库脚本或连接器特判。

## 1. 当前结论

平台已经具备策略任务、人工审批、文章保存、远端发布、SEO 元数据同步、效果观察和异常日志等基础能力。当前不能称为“完整可托管”的主要原因，不是缺少内容生成能力，而是统一控制面和真实连接器之间仍有契约断层。

本轮确认 4 个真实运行异常，其中 3 个是 P1；此外还有 6 项上线能力需求。此前 Strategy Run 的审计时间戳类型问题在当前源码中已增加显式 `timestamptz` 转换，本清单不再把它列为开放缺陷，但需要回归测试。

### 2026-07-28 当前状态矩阵

| 异常 | 当前状态 | 本轮源码结果 | 后续门禁 |
|---|---|---|---|
| ANOM-01 | 源码已修复，待真实验收 | OEMApps 原始 `1`、`"1"`、`publish`、`published` 已归一化为 `published`，同时保留原始状态 | 启动后端和 PG17 后完成真实创建、回读与幂等验收 |
| ANOM-02 | 源码已修复，待真实验收 | Shopify 已实现按 blog handle 与 article handle 查询；创建 mutation 只尝试一次；异常后只做远端只读回读，确认成功则恢复结果，未确认则保留原异常且不重复写 | 启动后端和 PG17 后验证已存在 handle、正常创建及“远端成功但客户端超时”三种场景 |
| ANOM-03 | 第二轮，仍开放 | 本轮未实现 Shopify 图片上传 | 第二轮接入 Shopify Files API 或 staged upload，并完成真实资产回读与幂等验收 |
| ANOM-04 | 源码已修复，待三站真实验收 | 公开 URL 统一解析；Shopify 内部域名改写为业务公开域；Exdivo、Avinoti、HealthyOxy 已增加 article、execution、publish task、effect task 四处一致性合同测试 | 启动后端和 PG17 后完成三站真实 URL 回读验收 |

## 2. 已确认异常

### ANOM-01：OEMApps 公开状态 `"1"` 被误判为未发布

- 级别：P1
- 当前状态：源码已修复，待后端、PG17 和真实 OEMApps 运行态验收。历史运行时曾通过独立远端回读临时恢复。
- 实际现象：远端 POST 成功并返回文章 ID，GET 回读的 `status` 为字符串 `"1"`；统一发布服务只接受 `publish/published/public`，因此把成功写入记录成失败。
- 风险：Agent 或人工看到失败后重试，可能重复创建文章。
- 涉及位置：
  - `app/services/publish_service.py::_verify_remote`
  - `app/clients/publishers.py::OpenAPIPublisher`
- 后端需求：
  1. 状态归一化必须由连接器契约负责，不允许统一层猜测平台原始值。
  2. OEMApps 至少把整数 `1`、字符串 `"1"`、`publish`、`published` 归一化为统一枚举 `published`。
  3. 原始状态保留在审计证据中，统一层只消费归一化状态。
- 验收标准：
  - 对状态 `1`、`"1"`、`published` 的参数化测试全部通过。
  - 真实 OEMApps 创建后，发布任务、文章任务和执行任务一次完成为 `done`。
  - 同一幂等键重试返回原文章 ID，不产生第二篇。

### ANOM-02：Shopify 新建文章前调用未实现的 slug 查重

- 级别：P1
- 当前状态：源码已修复，待后端、PG17 和真实 Shopify 运行态验收。HealthyOxy 历史运行曾通过同步远端库存后直接调用现有 Shopify Publisher 完成。
- 当前实现：按 blog handle 与 article handle 查询远端文章；创建 mutation 只尝试一次；若创建调用异常，则仅执行只读 handle 回读，确认远端事实后恢复为成功，不对写 mutation 做盲重试。
- 实际现象：统一发布服务在创建前无条件调用 `find_article_by_slug`；`ShopifyPublisher` 没有实现该方法，因此在远端写入前失败。
- 风险：Shopify 新建文章无法走统一发布闭环；绕过统一层会削弱幂等和审计。
- 涉及位置：
  - `app/services/publish_service.py::publish_article`
  - `app/clients/publishers.py::ShopifyPublisher`
- 后端需求：
  1. 为 Shopify 实现按 `blog handle + article handle` 的远端查询。
  2. 查重结果必须包含远端 GID、handle、公开状态和公开 URL。
  3. 创建流程必须使用稳定幂等键；请求超时后先按 handle/GID 回读，再决定是否重试。
  4. 能力快照中明确声明 `find_article_by_slug=true/false`，统一流程不得调用未声明能力。
- 验收标准：
  - 已存在 handle 返回原 GID并进入 `slug_reuse`，不创建重复文章。
  - 不存在 handle 时只创建一次，随后按 GID 回读。
  - 模拟“远端已成功、客户端超时”后恢复为成功，不重复写。

### ANOM-03：Shopify 站点不支持统一图片上传

- 级别：P2
- 当前状态：第二轮，仍开放。本轮不实现 Shopify 图片上传；历史运行使用 OEMApps CDN 临时托管。
- 实际现象：`POST /api/v1/sites/{site_id}/images/upload` 对 Shopify 返回 `connector shopify does not support upload_image`。
- 风险：图片资产不属于目标站点媒体库；后续迁移、治理和来源追踪困难。
- 涉及位置：
  - `app/api/v1/business.py::upload_site_image_route`
  - `app/clients/publishers.py::ShopifyPublisher`
- 后端需求：
  1. 接入 Shopify Files API 或 staged upload。
  2. 统一输入继续支持 `url/base64/file`，服务端负责转换为平台所需上传流程。
  3. 统一返回 `{ok, dry_run, site_id, image_id, src}`。
  4. 保存目标站点、原始内容哈希、远端 ID、最终 CDN URL 和上传时间。
  5. 相同站点、相同文件哈希、相同幂等键不得重复上传。
- 验收标准：
  - Avinoti、HealthyOxy 各上传一张 PNG，并能通过返回 ID 或 URL 回读。
  - 文章发布后图片 URL 属于对应 Shopify/CDN 资产体系。
  - 重复上传返回原资产，不新建副本。

### ANOM-04：连接器内部域名泄漏为公开追踪 URL

- 级别：P1
- 当前状态：源码已修复，待 Exdivo、Avinoti、HealthyOxy 三站真实运行态验收。历史运行曾手工把文章和效果任务改回公开主域名。
- 当前实现：三站均通过统一公开 URL 解析合同；已用独立字面期望验证 article、execution、publish task、effect task 四处 URL 一致。Shopify 的 `shopDomain` 可继续使用内部 `*.myshopify.com` API 域名，但公开 URL 使用站点 `domain/base_url` 业务域。
- 实际现象：
  - OEMApps 返回 `*.jcysaas.cn/blogs/detail/{id}`；
  - Shopify 返回 `*.myshopify.com/blogs/...`；
  - 这些地址会被写入 `articles.published_url` 和效果任务 `target_url`。
- 风险：GSC/GA4/索引观察使用错误域名，导致数据归因错误；也容易让运营误以为内容发布到了其他站点。
- 涉及位置：
  - `app/core/article_urls.py`
  - `app/services/publish_service.py`
  - `app/services/post_sync_service.py`
  - `app/services/strategy_effect_service.py`
- 后端需求：
  1. 站点能力快照必须声明 `public_base_url`、`canonical_hosts`、`article_url_template`。
  2. `published_url` 和效果任务只能保存通过 canonical host 校验的公开 URL。
  3. 连接器原始 detail URL 单独保存为 `remote_detail_url` 或审计证据，不能覆盖公开 URL。
  4. Shopify 必须把 myshopify 域名重写为业务公开域名；OEMApps 根据站点模板生成 slug 或 detail 路径。
  5. 若无法确定公开 URL，动作进入 Hold/P1，而不是保存内部域名。
- 验收标准：
  - Exdivo 只返回 `exdivo.com`。
  - Avinoti 只返回 `avinoti.shop`。
  - HealthyOxy 只返回 `healthyoxy.com`。
  - article、execution、publish task、effect task 四处 URL 完全一致。

## 3. 上线所需功能

### REQ-01：统一连接器能力契约

每个站点和每个动作返回机器可读能力：

- read/list/get/find/create/update/upload/sync-seo/readback；
- 是否需要审批；
- 支持字段；
- 公开 URL 规则；
- 原始状态到统一状态的映射；
- 幂等能力；
- 已验证时间和能力快照哈希。

能力未声明时默认禁止，不能在执行到一半后才发现方法未实现。

### REQ-02：所有真实写入必须由统一 Action 闭环消费

文章新建、更新、图片上传、产品/分类/首页 SEO 更新均走：

`preview → approve → execute → remote readback → local finalize → effect tracking`

Agent 不应再直接插入 review/execution/article 任务来补控制面缺口。人工明确下达任务可以作为审批来源，但仍需由后端生成完整审计链。

### REQ-03：端到端幂等与不确定结果恢复

- strategy run、action、publish、upload、metadata sync 各自接受幂等键；
- 子动作派生稳定子键；
- 同键同请求返回原结果；
- 同键不同请求返回稳定冲突码；
- 网络超时后先做远端事实回读；
- 记录 `confirmed_success / confirmed_failure / unknown_remote_state`；
- `unknown_remote_state` 禁止自动重试写入。

### REQ-04：真正的零写入模拟模式

需要一个不会写业务表、任务表、审计表，也不会调用远端写接口的 simulation：

- 返回站点覆盖矩阵、候选、预期 Action、能力缺口和预计写入；
- 可选择保存为本地文件，但默认数据库零写；
- 提供数据库前后事务计数或审计证明。

这与现有 `dry_run` 不同：现有 dry-run 仍可能创建 Run、Action 或审计记录。

### REQ-05：异步 Worker 与可恢复调度

`/start` 不应长期占用同步请求：

- 独立队列 worker；
- 租约、心跳、超时接管、旧 Token 拒绝；
- 进程重启后从持久化状态恢复；
- 每站独立执行，单站失败不阻断其他站；
- 支持取消未开始动作，不能把取消伪装成回滚。

### REQ-06：效果观察闭环

新文章和更新文章发布成功后自动创建：

- T+0：状态码、正文、图片、meta、canonical、robots、sitemap；
- T+7：抓取、索引、canonical；
- T+14/28/56：GSC、GA4、排名和转化变化；
- 必要时 T+90。

要求：

- 使用公开 canonical URL；
- 新文章使用结构性零基线；
- 更新文章保存发布前结构快照；
- 观察结果回写原 strategy/action/article；
- 异常不创建正向效果结论。

### REQ-07：全业务、全站点覆盖

每次“执行策略”必须先列出业务，再列出该业务下全部启用站点：

- 主站与博客站分别决策；
- 每站允许 `execute / hold / no-op / configuration_repair`；
- 不要求每站每天强制发布；
- 但任何站点都不能被静默遗漏；
- 覆盖矩阵和遗漏原因必须入库。

### REQ-08：数据证据和来源持久化

- Semrush 关键词只作为参考信号，不直接等于执行指令；
- GSC、GA4、SERP、站内库存、产品数据和公开搜索证据记录采集时间；
- SerpAPI 结果按现有 `serp_snapshots` 结构入库；
- SerpAPI 不可用时，公开搜索降级证据也需保存来源 URL、查询、采集时间和结果摘要；
- 过期证据触发重新取证，不沿用旧审批。

### REQ-09：认证和 business scope 授权

在服务离开可信本机边界前必须完成：

- 全局身份认证；
- actor 到 business/site 的授权；
- 所有读取和写入都校验 business scope；
- 审批人、执行人、恢复人可追踪；
- 凭据不出现在日志、API 响应和异常指纹中。

若系统始终只绑定 `127.0.0.1` 且由单一可信操作者使用，可作为本机版上线门禁，而不是阻塞当前本机运营。

### REQ-10：统一异常契约和运行报告

任何 Run 完成后返回：

- 成功、失败、Hold、无动作的站点数量；
- 每个动作的最终状态；
- 稳定错误码、严重级别、异常指纹；
- 是否发生远端写入；
- 是否存在不确定远端状态；
- 日志路径、逐篇日志、异常报告和最终证据路径。

相同根因应使用稳定指纹聚合，避免一次批量执行生成大量重复异常。

## 4. 暂不开放的操作

以下操作继续默认禁止，不作为本阶段上线前置：

- URL、slug、canonical、redirect、noindex 自动修改；
- 删除文章、产品或分类；
- 价格、库存、Variant、分类成员关系；
- 未经审批的真实回滚；
- 无审批的大规模自动发布。

这些操作以后必须使用独立高风险审批、执行前快照、回读和受控回滚。

## 5. 建议开发顺序

### 第一批：源码已完成，待运行态验收

1. ANOM-01 状态归一化：源码和参数化回归已完成，待真实 OEMApps 验收；
2. ANOM-02 Shopify handle 查重和超时后远端回读恢复：源码和回归已完成，待真实 Shopify 验收；
3. ANOM-04 公开 URL/canonical 单一来源：源码和三站四处合同测试已完成，待 Exdivo、Avinoti、HealthyOxy 真实验收；
4. 启动后端和 PG17，完成以上三项真实连接器运行态验收。

### 第二批：图片能力与统一执行闭环

1. ANOM-03 Shopify 图片上传；
2. 为 ANOM-03 增加真实连接器集成测试；
3. REQ-01 能力契约；
4. REQ-02 统一 Action 闭环；
5. REQ-03 全链路幂等；
6. REQ-06 自动效果观察；
7. REQ-07 全站覆盖。

### 第三批：正式托管能力

1. REQ-04 零写模拟；
2. REQ-05 独立 Worker；
3. REQ-08 证据持久化；
4. REQ-09 身份与业务授权；
5. REQ-10 异常和报告契约；
6. 人工审批模式连续观察 7 天；
7. 仅对明确低风险动作开放自动执行并连续观察 14 天。

## 6. 完整验收场景

后端交付后至少运行以下真实场景：

1. OEMApps 新建一篇测试文章，状态返回 `"1"`，统一流程一次成功。
2. 相同幂等键重复创建，返回同一远端 ID。
3. 模拟远端成功但客户端超时，系统通过回读恢复。
4. Shopify 已存在相同 handle，不重复创建。
5. Shopify 新建文章并同步 meta title/description，GID 回读一致。
6. Shopify 上传图片，重复请求不产生第二份资产。
7. 三个站点返回的公开 URL 均属于各自主域名。
8. 任一站点失败时，其他站点继续执行并各自落状态。
9. 新文章自动生成 T+0、7、14、28、56 天观察任务。
10. simulation 前后数据库业务表和任务表行数不变。
11. 能力快照变化后，旧审批失效并要求重新预览。
12. 进程在远端写入后、本地收尾前退出，重启后恢复且不重复写。

## 7. 后端交付物

- 变更说明与接口契约；
- 数据库迁移及 PG17 验证证据；
- 单元、集成、并发和恢复测试；
- 三类连接器的能力快照示例；
- 上述 12 个验收场景的实际输出；
- 已知限制和禁止操作清单；
- 不包含真实凭据的复现日志。
