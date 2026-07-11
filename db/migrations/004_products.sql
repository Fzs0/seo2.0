-- =====================================================================
-- SEO Workbench Migration 004: products 表
-- Depends : 001_agent_memory_schema.sql + 002_rule_engine.sql
-- Target  : PostgreSQL 14+
-- Idempotent: yes
--
-- 背景：
--   之前 §3 那一轮 sync_sites_and_products.py 把 104 个产品塞进了
--   seo_agent.posts 表。这违反了 posts 表的字段语义（"已发布文章" ≠ "产品 SKU"）。
--   本 migration：
--     1. 创建 seo_agent.products 表，字段与 product-assets.local.json 严格对齐
--     2. 提供必要的索引、外键、CHECK 约束
--     3. 不自动迁移数据 —— 迁移由 scripts/migrate_products_to_v2.mjs 完成
--        （先把错位数据从 posts 清掉，再灌进 products，保证可重入）
-- =====================================================================

SET search_path = seo_agent;

CREATE TABLE IF NOT EXISTS seo_agent.products (
  id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  external_id     varchar(64) NOT NULL,
  site_id         uuid        REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  handle          varchar(256),
  title           text        NOT NULL,
  url             text,
  category        varchar(128),
  price           numeric(10,2),
  status          varchar(16) NOT NULL DEFAULT 'active',
  image           jsonb       NOT NULL DEFAULT '{}'::jsonb,
  description     text,
  keywords        text[]      NOT NULL DEFAULT ARRAY[]::text[],
  source          varchar(64) NOT NULL DEFAULT 'product-assets.local.json',
  raw             jsonb       NOT NULL DEFAULT '{}'::jsonb,
  extracted_at    timestamptz NOT NULL DEFAULT now(),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT products_external_id_uk UNIQUE (external_id),
  CONSTRAINT products_status_chk CHECK (status IN ('active', 'draft', 'archived')),
  CONSTRAINT products_id_positive_chk CHECK (id > 0)
);

COMMENT ON TABLE seo_agent.products IS
  '产品主档。每行代表一个从 openapi.oemapps.com / WordPress / Shopify 等渠道抽取的产品 SKU。产品与文章语义分离：本表只管产品元数据，文章存于 posts / articles。';

COMMENT ON COLUMN seo_agent.products.id IS
  'bigint 自增主键；仅内部使用，外部引用用 external_id（业务唯一键）。';
COMMENT ON COLUMN seo_agent.products.external_id IS
  '业务唯一键，来自源系统的产品 ID（如 openapi.oemapps.com 返回的 id 字段）。';
COMMENT ON COLUMN seo_agent.products.site_id IS
  '所属主站 → sites.id，ON DELETE CASCADE。允许 NULL（跨多站的产品）。';
COMMENT ON COLUMN seo_agent.products.handle IS
  '产品 slug 来源（如 exdivo-bright-mirror-35k-replaceable-vape-kit）。';
COMMENT ON COLUMN seo_agent.products.title IS
  '产品标题（含口味变体，例如 "COOL MINT - ExDivo BRIGHT MIRROR 35K ..."）。';
COMMENT ON COLUMN seo_agent.products.url IS
  '产品 URL 路径或完整 URL。';
COMMENT ON COLUMN seo_agent.products.category IS
  '产品分类。源数据常为空，前端按需展示。';
COMMENT ON COLUMN seo_agent.products.price IS
  '单价 USD；源数据常为 null（依赖前端按 shop_id / locale 补价）。';
COMMENT ON COLUMN seo_agent.products.status IS
  '产品状态枚举：active / draft / archived。源数据 1 → active，0 → draft。';
COMMENT ON COLUMN seo_agent.products.image IS
  '产品图片对象 jsonb；源 openapi 接口返回 [object Object] 占位符，前端按需从 image.url 字段解析。';
COMMENT ON COLUMN seo_agent.products.description IS
  '产品描述，纯文本。';
COMMENT ON COLUMN seo_agent.products.keywords IS
  '产品关键词 text[]；源数据常为空，预留给后续 AI 提取。';
COMMENT ON COLUMN seo_agent.products.source IS
  '来源标识，例如 product-assets.local.json / openapi.oemapps.com / wordpress。';
COMMENT ON COLUMN seo_agent.products.raw IS
  '原始抓取数据 jsonb，便于回溯与字段映射升级。';
COMMENT ON COLUMN seo_agent.products.extracted_at IS
  '从源系统抽取的时间。';
COMMENT ON COLUMN seo_agent.products.created_at IS
  '行创建时间。';
COMMENT ON COLUMN seo_agent.products.updated_at IS
  '最近更新时间，由 set_updated_at 触发器维护。';

-- 索引
CREATE INDEX IF NOT EXISTS products_site_status_idx
  ON seo_agent.products (site_id, status);

CREATE INDEX IF NOT EXISTS products_category_idx
  ON seo_agent.products (category);

CREATE INDEX IF NOT EXISTS products_status_idx
  ON seo_agent.products (status);

CREATE INDEX IF NOT EXISTS products_raw_gin_idx
  ON seo_agent.products USING gin (raw jsonb_path_ops);

-- updated_at 触发器复用 v1 函数
DROP TRIGGER IF EXISTS set_products_updated_at ON seo_agent.products;
CREATE TRIGGER set_products_updated_at
BEFORE UPDATE ON seo_agent.products
FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();

-- 视图：业务侧只读"激活"产品
CREATE OR REPLACE VIEW seo_agent.v_active_products AS
SELECT *
FROM   seo_agent.products
WHERE  status = 'active';

COMMENT ON VIEW seo_agent.v_active_products IS
  '当前激活的产品视图（status=active）。前端默认入口，避免到处加 WHERE 条件。';