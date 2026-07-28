-- Local SQL copy of Alembic v5_google_data. Idempotent for Docker bootstrap.

CREATE TABLE IF NOT EXISTS seo_agent.google_sync_log (
  id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id       uuid        NOT NULL,
  site_id         uuid        REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  source_type     varchar(8)  NOT NULL,
  range_start     date        NOT NULL,
  range_end       date        NOT NULL,
  status          varchar(16) NOT NULL DEFAULT 'running',
  trigger         text        NOT NULL DEFAULT 'manual',
  rows_fetched    integer     NOT NULL DEFAULT 0,
  rows_written    integer     NOT NULL DEFAULT 0,
  duration_ms     integer,
  error_message   text,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  CONSTRAINT google_sync_log_type_chk CHECK (source_type IN ('gsc', 'ga4')),
  CONSTRAINT google_sync_log_status_chk CHECK (status IN ('running', 'done', 'failed', 'cancelled')),
  CONSTRAINT google_sync_log_trigger_chk
    CHECK (trigger IN ('manual', 'scheduled', 'retry', 'strategy_hold_refresh')),
  CONSTRAINT google_sync_log_id_positive_chk CHECK (id > 0)
);

CREATE INDEX IF NOT EXISTS google_sync_log_site_idx ON seo_agent.google_sync_log (site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS google_sync_log_source_idx ON seo_agent.google_sync_log (source_id, source_type, started_at DESC);
CREATE INDEX IF NOT EXISTS google_sync_log_status_idx ON seo_agent.google_sync_log (status, started_at DESC);

CREATE TABLE IF NOT EXISTS seo_agent.gsc_query_daily (
  site_id      uuid        NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  date         date        NOT NULL,
  query        text        NOT NULL,
  page         text        NOT NULL DEFAULT '',
  country      varchar(8)  NOT NULL DEFAULT '',
  device       varchar(16) NOT NULL DEFAULT '',
  clicks       integer     NOT NULL DEFAULT 0,
  impressions  integer     NOT NULL DEFAULT 0,
  ctr          numeric(8,4) NOT NULL DEFAULT 0,
  position     numeric(8,2) NOT NULL DEFAULT 0,
  synced_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, date, query, page, country, device)
);

CREATE INDEX IF NOT EXISTS gsc_query_daily_site_date_idx ON seo_agent.gsc_query_daily (site_id, date DESC);
CREATE INDEX IF NOT EXISTS gsc_query_daily_site_query_idx ON seo_agent.gsc_query_daily (site_id, query, date DESC);
CREATE INDEX IF NOT EXISTS gsc_query_daily_site_page_idx ON seo_agent.gsc_query_daily (site_id, page, date DESC);

CREATE TABLE IF NOT EXISTS seo_agent.ga4_session_daily (
  site_id              uuid        NOT NULL REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
  date                 date        NOT NULL,
  channel              varchar(32) NOT NULL DEFAULT 'all',
  sessions             integer     NOT NULL DEFAULT 0,
  total_users          integer     NOT NULL DEFAULT 0,
  new_users            integer     NOT NULL DEFAULT 0,
  pageviews            integer     NOT NULL DEFAULT 0,
  engaged_sessions     integer     NOT NULL DEFAULT 0,
  engagement_rate      numeric(8,4) NOT NULL DEFAULT 0,
  avg_session_duration numeric(10,2) NOT NULL DEFAULT 0,
  bounce_rate          numeric(8,4) NOT NULL DEFAULT 0,
  conversions          numeric(12,2) NOT NULL DEFAULT 0,
  revenue              numeric(14,2) NOT NULL DEFAULT 0,
  synced_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (site_id, date, channel)
);

CREATE INDEX IF NOT EXISTS ga4_session_daily_site_date_idx ON seo_agent.ga4_session_daily (site_id, date DESC);
CREATE INDEX IF NOT EXISTS ga4_session_daily_site_channel_idx ON seo_agent.ga4_session_daily (site_id, channel, date DESC);

CREATE OR REPLACE VIEW seo_agent.v_gsc_site_28d AS
SELECT site_id,
       sum(clicks) AS clicks,
       sum(impressions) AS impressions,
       CASE WHEN sum(impressions) > 0 THEN round(sum(clicks)::numeric / sum(impressions), 4) ELSE 0 END AS ctr,
       CASE WHEN sum(impressions) > 0 THEN round(sum(position * impressions) / sum(impressions), 2) ELSE 0 END AS avg_position
FROM seo_agent.gsc_query_daily
WHERE date >= current_date - INTERVAL '28 days'
GROUP BY site_id;

CREATE OR REPLACE VIEW seo_agent.v_gsc_query_28d AS
SELECT site_id,
       query,
       sum(clicks) AS clicks,
       sum(impressions) AS impressions,
       CASE WHEN sum(impressions) > 0 THEN round(sum(clicks)::numeric / sum(impressions), 4) ELSE 0 END AS ctr,
       CASE WHEN sum(impressions) > 0 THEN round(sum(position * impressions) / sum(impressions), 2) ELSE 0 END AS avg_position,
       max(date) AS last_seen
FROM seo_agent.gsc_query_daily
WHERE date >= current_date - INTERVAL '28 days'
  AND query <> ''
GROUP BY site_id, query;

CREATE OR REPLACE VIEW seo_agent.v_gsc_page_28d AS
SELECT site_id,
       page,
       sum(clicks) AS clicks,
       sum(impressions) AS impressions,
       CASE WHEN sum(impressions) > 0 THEN round(sum(clicks)::numeric / sum(impressions), 4) ELSE 0 END AS ctr,
       CASE WHEN sum(impressions) > 0 THEN round(sum(position * impressions) / sum(impressions), 2) ELSE 0 END AS avg_position,
       max(date) AS last_seen
FROM seo_agent.gsc_query_daily
WHERE date >= current_date - INTERVAL '28 days'
  AND page <> ''
GROUP BY site_id, page;

CREATE OR REPLACE VIEW seo_agent.v_ga4_site_28d AS
SELECT site_id,
       sum(sessions) AS sessions,
       sum(total_users) AS users,
       sum(new_users) AS new_users,
       sum(pageviews) AS pageviews,
       CASE WHEN sum(sessions) > 0 THEN round(sum(engaged_sessions)::numeric / sum(sessions), 4) ELSE 0 END AS engagement_rate,
       CASE WHEN sum(sessions) > 0 THEN round(sum(bounce_rate * sessions) / sum(sessions), 4) ELSE 0 END AS bounce_rate,
       round(sum(avg_session_duration * sessions)::numeric / NULLIF(sum(sessions), 0), 2) AS avg_session_duration,
       sum(conversions) AS conversions,
       sum(revenue) AS revenue
FROM seo_agent.ga4_session_daily
WHERE date >= current_date - INTERVAL '28 days'
  AND channel = 'all'
GROUP BY site_id;
