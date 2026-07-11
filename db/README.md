# `db/` 目录说明

本目录包含 PostgreSQL 记忆库的所有 schema migration 与 seed 脚本。

所有 DDL 在 `migrations/` 下按编号顺序执行。所有脚本都假设目标 PG 版本 ≥ 14（与 `001_agent_memory_schema.sql` 顶部声明一致）。

---

## 1. 文件清单

| 文件 | 用途 |
| --- | --- |
| `migrations/001_agent_memory_schema.sql` | v1 业务表：`sites` / `keywords` / `posts` / `serp_snapshots` / `tasks` / `articles`（已存在，本轮不动） |
| `migrations/002_rule_engine.sql` | v2 规则引擎表：`rule_sets` / `rule_field_map` / `rule_audit_log` + 2 个视图 + 3 个触发器 |
| `migrations/003_localize_comments.sql` | 全量 COMMENT 中文化：v1 + v2 全部表、列、视图的注释。仅改注释、不改 schema，幂等可重跑 |
| `scripts/seed_rule_baseline.mjs` | 把 `workflows/seo-standard.json` 灌入一次 baseline 的 Node.js 脚本 |

---

## 2. 部署顺序

### 2.1 全新部署

```bash
# 1. 跑 v1（如果数据库里还没有 seo_agent schema）
psql "$DATABASE_URL" -f db/migrations/001_agent_memory_schema.sql

# 2. 跑 v2（建表 + 索引 + 触发器 + 视图，COMMENT 为英文）
psql "$DATABASE_URL" -f db/migrations/002_rule_engine.sql

# 3. 中文化所有 COMMENT（覆盖 v1 + v2 全部表/列/视图）
psql "$DATABASE_URL" -f db/migrations/003_localize_comments.sql

# 4. 灌一次 baseline
DATABASE_URL="$DATABASE_URL" node db/scripts/seed_rule_baseline.mjs
```

### 2.2 已有 v1、补 v2

```bash
psql "$DATABASE_URL" -f db/migrations/002_rule_engine.sql
psql "$DATABASE_URL" -f db/migrations/003_localize_comments.sql
DATABASE_URL="$DATABASE_URL" node db/scripts/seed_rule_baseline.mjs
```

### 2.3 仅重灌 baseline

```bash
# 幂等：重复跑是 no-op，不会覆盖已有行
DATABASE_URL="$DATABASE_URL" node db/scripts/seed_rule_baseline.mjs
```

---

## 3. seed 脚本行为约定

`seed_rule_baseline.mjs` 是**保守**的：

| 场景 | 行为 |
| --- | --- |
| 已有同 `(name='seo-standard', source='file', version)` 的行 | 打印 `skip` + 已有行 id，退出 0 |
| 同 family 已有别的行 `is_active=true` | 打印 `conflict` + 当前 active id / version，**不覆盖**，退出 1 |
| 全新 baseline | 在同一事务里写 1 行 `rule_sets` + 2 行 `rule_audit_log`（`create` + `activate`），提交后打印 `done` |
| `DATABASE_URL` 未设置 | 打印 `init` 错误，退出 1，不连 DB |
| `version` 不符合 SemVer | 打印 `validate` 错误并退出（避免 PG 端 `rule_sets_version_chk` 抛错） |

输出格式是结构化的 `[stage] message {json_extra}`，方便运维抓取 stage 与 id。

---

## 4. 验证脚本

跑完后人工 / 监控核对：

```sql
-- 当前激活版本
SELECT id, name, version, source, effective_at
  FROM seo_agent.v_active_rule_set;

-- baseline 落库行数
SELECT count(*) FROM seo_agent.rule_sets;

-- 审计日志最近 5 条
SELECT id, rule_set_id, action, actor, created_at
  FROM seo_agent.rule_audit_log
 ORDER BY created_at DESC
 LIMIT 5;
```

预期：

- `v_active_rule_set` 恰好返回 1 行，`source='file'`，`version='0.2.0'`。
- `rule_sets` 行数 ≥ 1（首跑 = 1，重跑不变）。
- `rule_audit_log` 中能看到 `create` + `activate` 两条 actor 以 `seed:` 开头的记录。

---

## 5. 回滚

推荐用 **Alembic**（见 §6）一键回滚，不要手写 DROP：

```bash
alembic downgrade -1    # 回滚一个版本
alembic downgrade base  # 回到最初
```

**仅测试环境手写 DROP**（生产禁用）：

```sql
DROP VIEW  IF EXISTS seo_agent.v_active_rule_field_map;
DROP VIEW  IF EXISTS seo_agent.v_active_rule_set;
DROP TABLE IF EXISTS seo_agent.rule_audit_log;
DROP TABLE IF EXISTS seo_agent.rule_field_map;
DROP TABLE IF EXISTS seo_agent.rule_sets;
DROP FUNCTION IF EXISTS seo_agent.check_default_value_type();
-- set_updated_at() 是 v1 的函数，不要在这里 DROP
```

生产环境回滚请走备份恢复，不要直接 DROP。

---

## 6. 与 docs 的关系

- 设计意图：`docs/rule-extraction.md §5`
- 落地步骤：`docs/rule-extraction.md §9`
- 兼容性约束：`docs/rule-extraction.md §8`