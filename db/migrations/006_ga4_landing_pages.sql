CREATE TABLE IF NOT EXISTS seo_agent.ga4_landing_page_daily (
  site_id              uuid        NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  date                 date        NOT NULL,
  landing_page         text        NOT NULL,
  sessions             integer     NOT NULL DEFAULT 0,
  total_users          integer     NOT NULL DEFAULT 0,
  pageviews            integer     NOT NULL DEFAULT 0,
  engaged_sessions     integer     NOT NULL DEFAULT 0,
  engagement_rate      numeric(8,4) NOT NULL DEFAULT 0,
  avg_session_duration numeric(10,2) NOT NULL DEFAULT 0,
  bounce_rate          numeric(8,4) NOT NULL DEFAULT 0,
  conversions          numeric(12,2) NOT NULL DEFAULT 0,
  revenue              numeric(14,2) NOT NULL DEFAULT 0,
  synced_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, date, landing_page)
);
CREATE INDEX IF NOT EXISTS ga4_landing_page_daily_site_date_idx ON seo_agent.ga4_landing_page_daily (site_id, date DESC);
