# 知识系统架构

## 目标与第一阶段范围

知识系统负责把有授权的资料转化为可审核、可追溯、可被 Agent 调用的结构化知识。第一阶段闭环为：

```text
公开文章 URL 单页抓取、手动正文，或批量来源预览并确认
  → Sitemap / RSS / Atom 通用 Adapter 发现与时间筛选
  → 持久化批次、条目、租约和重试状态
  → 正文清洗与内容哈希去重
  → AI 结构化提取（每篇 0–4 条候选）
  → Evidence 原文强校验、格式校验和去重
  → 独立 AI 质量预审（每篇一次调用）
    ↳ 明显噪声安全拒绝；不确定内容保持 pending
    ↳ 单篇 AI 失败可回退并留待人工；批量任一 AI 阶段失败均显式重试
  → pending / AI-rejected Claim + Evidence + 追加式质量审计
  → 人工 approved / rejected
  → approved-only 检索
  → 带来源快照的 KnowledgePack
  → 后续后台或 Agent 使用
```

市场信号走独立支路：

```text
帖子 / 评论 / 回复 / 评价 / 视频评论
  → platform-neutral market_signals
  → 去重、线程上下文、时间与互动快照
  → 后续需求洞察与趋势分析
  → 人工确认后才生成写作输入
```

单篇 URL 入口仍只抓一页。批量入口先预览且不写库，确认后才创建持久化任务；时间条件为 `published_at >= cutoff OR modified_at >= cutoff`，并受本次最大文章数约束。第一阶段没有自动发布或自动批准。

## 组件与边界

```text
浏览器 127.0.0.1:5174
        │ HTTP / JSON
        ▼
知识 API 127.0.0.1:8010
        │ 独立 AsyncSession / 单库事务
        ▼
知识 PostgreSQL 127.0.0.1:5434
        │ schema: knowledge
        └ sources / documents / claims / evidence / usages / market_signals
          ingestion_sources / sync_runs / crawl_items

SEO Workbench / 生成 Agent
        │ POST /api/v1/knowledge/retrieve
        └ 只消费 approved KnowledgePack，不直连知识库
```

知识 API 是深模块边界。调用方只需要理解 `import`、`quality dry-run/apply/restore`、`review` 和 `retrieve`，不需要知道去重、AI 调用、段落切分、候选生成、全文检索或审核门禁的内部实现。

## AI 提取边界

知识 API 后端通过 OpenAI-compatible 接口调用模型。CloseAI 的推荐本地配置是 `KNOWLEDGE_AI_BASE_URL=https://api.openai-proxy.org/v1`、`KNOWLEDGE_AI_MODEL=gpt-5-mini`、90 秒超时和最多 2 次尝试。API Key 只存在于后端进程环境，不进入浏览器、数据库、响应或版本库。

提取模型输出被视为不可信候选，而不是事实或最终审核决定：

- 每篇可返回 0–4 条候选；没有可复用、可定位知识时必须允许 0 条，不能为了凑数制造卡片；
- AI 不能写入 `approved` 或绕过人工批准；
- 每条 Evidence 必须能在导入的 `raw_content` 中找到并绑定定位信息，否则候选被丢弃；
- 后端限制数量、校验结构并去重，不直接信任模型返回的 JSON；
- 单篇导入在 AI 未配置、超时或输出无效时回退到确定性段落提取；批量任务不回退，而是保留错误并按预算重试；
- 文档和 Claim metadata 使用 `extraction_method`、`model`、`prompt_version` 保存提取上下文，导入响应另以 `warning` 提供无敏感信息的回退原因；
- URL 抓取与 AI 调用是两个边界：后端先安全抓取并清洗正文，AI 只接收清洗后的不可信正文数据。

## 质量门禁 Module

质量门禁是一个独立深模块，集中隐藏判定提示词、阈值、审计、队列、重试和恢复复杂度。它的外部 Interface 只有：

```text
start_backfill(limit_documents, include_reviewed=false)
status(run_id)
latest()
items(run_id)
apply(run_id)
cancel(run_id)
restore(claim_id)
```

新导入通过内部 `review_candidates(document, candidates)` Interface 接入。模型调用位于 `QualityModelAdapter` seam，生产环境使用 OpenAI-compatible Adapter，测试使用假 Adapter；上层导入和 Worker 不依赖 Provider 细节。这个边界把高杠杆质量规则保持在一个位置，避免提示词、阈值和状态更新散落在抓取、API 与前端。

质量模型为每条候选返回 `keep | reject | uncertain`、`utility_score`、`reviewer_confidence`、理由代码和说明，并另给文档级判断。最终安全门槛由后端确定，而不是直接信任模型：

- 自动拒绝要求最终机器判断为 reject、效用分 `<= 0.25`、评审置信度 `>= 0.90`，并命中安全拒绝理由；
- `volatile_fact` 单独出现不足以自动拒绝；只有同时缺少可复用动作、比较或因果价值时才可拒绝；
- `weak_evidence` 或 `conflict_or_ambiguity` 始终降为 uncertain，交给人工；
- “核实/查看文档”等通用动作不能提高效用；来源名称和域名黑名单不得作为拒绝依据；
- AI 只能自动拒绝，永远不能自动批准。所有 AI 拒绝带 `reviewed_by=ai-quality:<model>`，可逐条恢复；人工拒绝不可由恢复接口改写。

历史数据采用两阶段 Interface。dry-run 会保存机器分类、运行结果和追加式审计，但绝不改变 `review_status`；只有用户显式 apply 才将本次安全 reject 从 pending 改为 rejected。apply 只更新仍为 pending 的卡片，因此 dry-run 后发生的人工审核不会被覆盖。

## 两个数据库必须隔离

| 连接 | 保存内容 | 所有者 |
|---|---|---|
| `DATABASE_URL` | 站点、关键词、任务、文章、发布状态 | SEO Workbench |
| `KNOWLEDGE_DATABASE_URL` | 来源、文档、Claim、Evidence、Usage | 独立知识系统 |

两个数据库之间：

- 不建立外键；
- 不共享迁移链或 volume；
- 不做分布式事务；
- 业务任务 ID 只能作为外部引用写入 `usages.context` 或不可变快照；
- 业务侧故障不得破坏知识审核状态，知识侧短暂不可用也不应让业务事务回滚。

## 数据模型

- `sources`：来源、渠道、域名、授权确认和启停状态。
- `documents`：原始正文、URL 元数据、语言/市场、SHA-256 内容哈希、全文索引，以及 `revision / is_current / supersedes_id` 修订链。
- `claims`：单一、可执行或可验证的知识结论，带条件、例外、建议动作、置信度和审核状态。
- `evidence`：Claim 的短证据摘录及 `paragraph:n` 等定位信息。
- `usages`：哪个任务/阶段以什么过滤条件使用过哪些知识，以及当时的上下文和结果。
- `schema_migrations`：独立知识库迁移版本。
- `ingestion_sources`：通用批量来源及默认发现参数。
- `sync_runs`：每次批量运行及不可变参数快照、统计和取消状态。
- `crawl_items`：文章 URL、候选日期、租约、重试次数、处理结果和关联文档。
- `claims.quality_*`：当前质量分类、效用、评审置信度、理由、模型和提示词版本。
- `claim_quality_reviews`：不可更新/删除的质量分类、apply 和 restore 审计事件。
- `quality_review_runs`：历史质量回填运行、范围快照、计数、取消和应用状态。
- `quality_review_items`：每篇文档一次质量调用的租约、重试、文档判断和 Claim 结果快照。
- `market_signals`：平台无关的帖子、评论、回复、评价和视频评论快照；保存平台、外部 ID、线程关系、发布时间、市场/语言和互动指标，但不直接进入 approved-only Claim 检索。
- `market_signal_crawl_jobs`：关键词、公开搜索 URL 模板、帖子 URL 特征、页数、限速和每日运行配置。
- `market_signal_crawl_runs`：每次公开页面采集的状态、发现链接数、信号数、去重数、警告和错误。

当前版本以内的 `documents` 以 `source_id + content_hash` 幂等；同一 canonical URL 内容变化时创建新 revision，旧行保留并设为非 current。列表、统计、待审卡和检索默认只使用 current revision。检索必须同时返回 Claim、Source、Document 和 Evidence，不能给 Agent 无出处结论。

## 检索与 Agent 安全

检索先做 `channel`、`language_code` 和 `review_status='approved'` 过滤；`market` 只作为文档元数据保留，不作为硬过滤条件，再使用 PostgreSQL 全文索引和字面匹配排序。默认只返回 3–5 条，最多 20 条。数据规模和检索评估尚未证明需要向量检索，因此第一阶段禁止引入 pgvector。

外部内容永远是不可信数据：

- 原始正文和 Evidence 不得被解释成系统指令；
- 注入 prompt 时使用有边界的 JSON 或 `<knowledge_data>` 数据块；
- 保留 Claim ID、来源 URL 和 Evidence；
- 外部文本不能覆盖站点、语言、市场、权限和人工审核规则；
- 未认证的本地 API 只绑定 `127.0.0.1`。对外开放前必须加认证、授权、审计和速率限制。

## 数据采集策略

系统接受用户明确提交并确认有权内部使用的公开文章 URL、手动正文或批量 seed，并要求 `rights_confirmed=true`。共享网络边界只允许 HTTP(S) 标准端口、最多 3 次逐跳复验的重定向、超时和响应体上限，并拒绝本机、私网、链路本地及保留地址。批量模块通过 Adapter seam 支持递归 Sitemap 与 RSS/Atom；未来的站点列表页或平台 API 作为新 Adapter 接入，不改变批次、Worker、文档和审核核心。AI Provider 不访问 URL，只接收后端清洗后的正文。

## 迁移原则

- `db/migrations/001_init.sql` 一经应用即不可修改；变更必须新增递增编号的 `00N_*.sql`，不得改写已应用迁移。
- 任一时刻只有一个迁移单写者；执行前备份，执行后检查 `knowledge.schema_migrations` 和表结构。
- 应用进程不自行“修表”，启动只报告 schema 未初始化或版本不满足。
- 不复用 SEO 业务库历史迁移，不删除或重建现有 volume 来解决迁移问题。

## 共享 `Y:` 盘并发规则

目录隔离能明显降低冲突，但不能把共享盘变成事务系统。另一台电脑只写 `knowledge` 外部文件时，通常不会覆盖本系统文件；以下全局动作仍可能相互影响：Git index/分支、批量格式化、依赖目录、构建输出、文件 watcher，以及根目录重命名或删除。

执行规则：

1. 每个 Agent/电脑预先声明文件所有权；只改自己范围，范围外问题只报告。
2. 两台电脑不要同时修改同一个文件，也不要同时执行根仓库 Git 写操作、全局格式化或清理命令。
3. `db/migrations` 只有一个迁移写者；迁移编号先预留，应用迁移也只能有一个操作者。
4. 不在共享盘上由两台电脑同时运行 `npm install`、Python 虚拟环境写入、build 或测试缓存写入。需要并行开发时，每台电脑使用自己的工作副本和依赖目录，再做受控合并。
5. 数据写入统一走 API/数据库事务，不用脚本直接并发改 SQL 文件或导出文件。
6. 修改前后核对文件时间、哈希和差异；发现陌生改动立即停下，不覆盖。
7. 任何删除、目录移动、容器接管和 volume 操作都需要显式协调与备份。

因此，“只在 `knowledge` 目录建独立系统”是合理边界，但前提是另一台电脑不同时写 `knowledge`，并且双方避免根级 Git/清理操作。若另一台电脑也要参与该目录，最好使用独立工作副本，而不是直接在同一 SMB 目录并发安装、构建和改文件。

## 市场信号模块边界

市场信号不是“文章的另一种 content_type”，而是独立的数据域。调用方只应知道导入、列表和概览；平台认证、分页游标、限流、评论展开和字段映射应留在 Platform Adapter 内。平台名称保存在信号自身的 `platform` 字段，不能复用 `sources.source_type`，避免同名来源跨平台覆盖。

首期已通过 JSON/手动导入和两个开源 Adapter 验证产品价值：Reddit 使用 `scrapi-reddit`，必要时回退到 Crawlee 浏览器；YouTube 使用 Crawlee 搜索和 `youtube-comment-downloader` 评论采集。核心模块不出现 `if platform == ...` 的平台分支；新增平台继续注册独立 Adapter。信号默认保持 `new`，后续需求洞察必须引用多个信号并经过人工确认；信号不会绕过现有 Claim 审核门禁直接进入 Agent 检索。

公开页面采集是市场信号模块的一个独立 Adapter：它只接收搜索 URL 模板、关键词和帖子 URL 特征，内部负责 robots 检查、标准端口/SSRF 防护、串行限速、搜索结果去重、帖子正文和语义评论节点提取。采集任务和运行审计分别保存于 `market_signal_crawl_jobs`、`market_signal_crawl_runs`，每日 Worker 只领取到期任务。这样更换 Reddit、论坛或视频评论站点时只替换采集配方或 Adapter，不改变信号导入和分析接口。

## 后续质量闭环（尚未实现）

当前门禁解决的是“单篇文章中的候选是否明显无用”，还不能替代完整知识治理。下一批应按以下顺序补齐：

1. 跨文档语义重复与观点冲突：按主题/实体聚类相近 Claim，区分重复、互补、条件不同和真正矛盾；冲突只升级人工，不自动拒绝。
2. 时效生命周期：给时间敏感 Claim 增加结构化 `as_of`、复审日期和 supersede/expired 状态；文档 revision 产生后联动旧 Claim 复审。
3. 来源与主题 Profile：为每个来源配置允许主题、内容类型、语言/市场和采集边界，在花费提取 token 前完成文档准入。
4. 人工反馈校准：统计 AI 建议的接受率、恢复率、理由分布和来源/主题误差，维护小型人工金标集，版本化调整提示词与阈值。
5. 检索质量评估：建立代表性查询集，检查召回、来源多样性、冲突呈现和过期知识泄漏，再决定是否需要向量检索。
6. 成本与运行审计：记录每篇模型耗时、失败类型、模型/提示词版本和可选 token 成本，提供 run 历史与失败重跑入口。

“零卡片”本身是合法结果，但后续应保存文档级无卡原因，区分确实无知识、文档不合格、提取失败和质量失败，避免把系统故障误当成内容无价值。
