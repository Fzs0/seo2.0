"""v4 Products: 产品主档表（替代 003 之前借 posts 存产品的设计）。

rev: e614f00c86b5
depends: f252ae2221b1
"""
from alembic import op

revision = "e614f00c86b5"
down_revision = "f252ae2221b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_site_status_idx ON seo_agent.products (site_id, status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_category_idx ON seo_agent.products (category);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_status_idx ON seo_agent.products (status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_raw_gin_idx ON seo_agent.products USING gin (raw jsonb_path_ops);"
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS set_products_updated_at ON seo_agent.products;
        CREATE TRIGGER set_products_updated_at BEFORE UPDATE ON seo_agent.products
        FOR EACH ROW EXECUTE FUNCTION seo_agent.set_updated_at();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_active_products AS
        SELECT *
        FROM   seo_agent.products
        WHERE  status = 'active';
        """
    )

    # 表 + 列中文注释
    op.execute(
        "COMMENT ON TABLE seo_agent.products IS '产品主档。每行代表一个从 openapi.oemapps.com / WordPress / Shopify 等渠道抽取的产品 SKU。产品与文章语义分离：本表只管产品元数据，文章存于 posts / articles。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.id IS 'bigint 自增主键；仅内部使用，外部引用用 external_id（业务唯一键）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.external_id IS '业务唯一键，来自源系统的产品 ID（如 openapi.oemapps.com 返回的 id 字段）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.site_id IS '所属主站 → sites.id，ON DELETE CASCADE。允许 NULL（跨多站的产品）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.handle IS '产品 slug 来源（如 exdivo-bright-mirror-35k-replaceable-vape-kit）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.title IS '产品标题（含口味变体，例如 COOL MINT - ExDivo BRIGHT MIRROR 35K ...）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.url IS '产品 URL 路径或完整 URL。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.category IS '产品分类。源数据常为空，前端按需展示。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.price IS '单价 USD；源数据常为 null（依赖前端按 shop_id / locale 补价）。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.status IS '产品状态枚举：active / draft / archived。源数据 1 → active，0 → draft。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.image IS '产品图片对象 jsonb；源 openapi 接口返回 [object Object] 占位符，前端按需从 image.url 字段解析。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.description IS '产品描述，纯文本。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.keywords IS '产品关键词 text[]；源数据常为空，预留给后续 AI 提取。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.source IS '来源标识，例如 product-assets.local.json / openapi.oemapps.com / wordpress。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.raw IS '原始抓取数据 jsonb，便于回溯与字段映射升级。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.extracted_at IS '从源系统抽取的时间。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.created_at IS '行创建时间。';"
    )
    op.execute(
        "COMMENT ON COLUMN seo_agent.products.updated_at IS '最近更新时间，由 set_updated_at 触发器维护。';"
    )
    op.execute(
        "COMMENT ON VIEW seo_agent.v_active_products IS '当前激活的产品视图（status=active）。前端默认入口，避免到处加 WHERE 条件。';"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS seo_agent.v_active_products;")
    op.execute("DROP TABLE IF EXISTS seo_agent.products CASCADE;")