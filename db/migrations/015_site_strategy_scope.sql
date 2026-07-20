ALTER TABLE seo_agent.sites
  ADD COLUMN IF NOT EXISTS business_id text,
  ADD COLUMN IF NOT EXISTS strategy_enabled boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS sites_strategy_scope_idx
  ON seo_agent.sites (business_id, strategy_enabled, status);

COMMENT ON COLUMN seo_agent.sites.business_id IS '业务归属标识；策略扫描和生成必须显式指定。';
COMMENT ON COLUMN seo_agent.sites.strategy_enabled IS '是否允许该站点参与所属业务的策略扫描和规划。';
