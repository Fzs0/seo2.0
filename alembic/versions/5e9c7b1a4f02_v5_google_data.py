"""v5 Google Data: 3 张表存储 GSC/GA4 同步数据 + 同步日志。

设计原则：
- 每条记录都是 (site_id, date, dimension_key...) → metrics 的窄长表（写入简单、聚合走 SQL）
- 重复同步走 ON CONFLICT DO UPDATE（最新数据覆盖旧值），避免日积月累
- source_id 关联 config/google-data-sources.local.json 里的 source.id，方便溯源

rev: 5e9c7b1a4f02
depends: e614f00c86b5
"""
from alembic import op

revision = "5e9c7b1a4f02"
down_revision = "e614f00c86b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. google_sync_log：同步任务执行日志（一次同步 = 一行）
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_agent.google_sync_log (
          id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          source_id       uuid        NOT NULL,
          site_id         uuid        REFERENCES seo_agent.sites(id) ON DELETE CASCADE,
          source_type     varchar(8)  NOT NULL,
          range_start     date        NOT NULL,
          range_end       date        NOT NULL,
          status          varchar(16) NOT NULL DEFAULT 'running',
          trigger         varchar(16) NOT NULL DEFAULT 'manual',
          rows_fetched    integer     NOT NULL DEFAULT 0,
          rows_written    integer     NOT NULL DEFAULT 0,
          duration_ms     integer,
          error_message   text,
          started_at      timestamptz NOT NULL DEFAULT now(),
          finished_at     timestamptz,

          CONSTRAINT google_sync_log_type_chk
            CHECK (source_type IN ('gsc', 'ga4')),
          CONSTRAINT google_sync_log_status_chk
            CHECK (status IN ('running', 'done', 'failed', 'cancelled')),
          CONSTRAINT google_sync_log_trigger_chk
            CHECK (trigger IN ('manual', 'scheduled', 'retry')),
          CONSTRAINT google_sync_log_id_positive_chk
            CHECK (id > 0)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS google_sync_log_site_idx ON seo_agent.google_sync_log (site_id, started_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS google_sync_log_source_idx ON seo_agent.google_sync_log (source_id, source_type, started_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS google_sync_log_status_idx ON seo_agent.google_sync_log (status, started_at DESC);"
    )

    # 2. gsc_query_daily：GSC searchanalytics 的 query × page × country × device 维度
    #   - dimensions 可空：空行表示 query 聚合（不按 page/country/device 切）
    #   - PRIMARY KEY 用 (site_id, date, query, page, country, device) 唯一约束
    op.execute(
        """
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS gsc_query_daily_site_date_idx ON seo_agent.gsc_query_daily (site_id, date DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS gsc_query_daily_site_query_idx ON seo_agent.gsc_query_daily (site_id, query, date DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS gsc_query_daily_site_page_idx ON seo_agent.gsc_query_daily (site_id, page, date DESC);"
    )

    # 3. ga4_session_daily：GA4 站点流量核心指标 + 来源渠道
    op.execute(
        """
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ga4_session_daily_site_date_idx ON seo_agent.ga4_session_daily (site_id, date DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ga4_session_daily_site_channel_idx ON seo_agent.ga4_session_daily (site_id, channel, date DESC);"
    )

    # 4. 视图：最近 28 天 GSC 聚合（站点级 + 关键词级 + 页面级）
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_gsc_site_28d AS
        SELECT site_id,
               sum(clicks)      AS clicks,
               sum(impressions) AS impressions,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(clicks)::numeric / sum(impressions), 4)
                    ELSE 0 END   AS ctr,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(position * impressions) / sum(impressions), 2)
                    ELSE 0 END   AS avg_position
          FROM seo_agent.gsc_query_daily
         WHERE date >= current_date - INTERVAL '28 days'
         GROUP BY site_id;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_gsc_query_28d AS
        SELECT site_id,
               query,
               sum(clicks)      AS clicks,
               sum(impressions) AS impressions,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(clicks)::numeric / sum(impressions), 4)
                    ELSE 0 END   AS ctr,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(position * impressions) / sum(impressions), 2)
                    ELSE 0 END   AS avg_position,
               max(date)        AS last_seen
          FROM seo_agent.gsc_query_daily
         WHERE date >= current_date - INTERVAL '28 days'
           AND query <> ''
         GROUP BY site_id, query;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_gsc_page_28d AS
        SELECT site_id,
               page,
               sum(clicks)      AS clicks,
               sum(impressions) AS impressions,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(clicks)::numeric / sum(impressions), 4)
                    ELSE 0 END   AS ctr,
               CASE WHEN sum(impressions) > 0
                    THEN round(sum(position * impressions) / sum(impressions), 2)
                    ELSE 0 END   AS avg_position,
               max(date)        AS last_seen
          FROM seo_agent.gsc_query_daily
         WHERE date >= current_date - INTERVAL '28 days'
           AND page <> ''
         GROUP BY site_id, page;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW seo_agent.v_ga4_site_28d AS
        SELECT site_id,
               sum(sessions)         AS sessions,
               sum(total_users)      AS users,
               sum(new_users)        AS new_users,
               sum(pageviews)        AS pageviews,
               CASE WHEN sum(sessions) > 0
                    THEN round(sum(engaged_sessions)::numeric / sum(sessions), 4)
                    ELSE 0 END       AS engagement_rate,
               CASE WHEN sum(sessions) > 0
                    THEN round(sum(bounce_rate * sessions) / sum(sessions), 4)
                    ELSE 0 END       AS bounce_rate,
               round(sum(avg_session_duration * sessions)::numeric
                     / NULLIF(sum(sessions), 0), 2) AS avg_session_duration,
               sum(conversions)      AS conversions,
               sum(revenue)          AS revenue
          FROM seo_agent.ga4_session_daily
         WHERE date >= current_date - INTERVAL '28 days'
           AND channel = 'all'
         GROUP BY site_id;
        """
    )

    # 中文注释（让 admin/inspect 时一眼看懂字段含义）
    op.execute(
        "COMMENT ON TABLE seo_agent.google_sync_log IS 'Google 数据同步日志：每次手动或调度同步 GSC/GA4 写一行。status=running/done/failed/cancelled，错误信息存 error_message。';"
    )
    op.execute(
        "COMMENT ON TABLE seo_agent.gsc_query_daily IS 'Google Search Console searchanalytics 日维度（query × page × country × device）。clicks/impressions/ctr/position 直接来自 API。';"
    )
    op.execute(
        "COMMENT ON TABLE seo_agent.ga4_session_daily IS 'GA4 Data API runReport 日维度（site × date × channel）。channel=all 是站点汇总，其他值是细分渠道（organic_search / direct / referral …）。';"
    )
    op.execute(
        "COMMENT ON VIEW seo_agent.v_gsc_site_28d IS '近 28 天 GSC 站点级聚合：clicks/impressions/ctr/avg_position。Dashboard 卡片直接查这个视图。';"
    )
    op.execute(
        "COMMENT ON VIEW seo_agent.v_gsc_query_28d IS '近 28 天 GSC 关键词级聚合。OpportunitiesPage 的高展示低点击洞察直接查这个视图。';"
    )
    op.execute(
        "COMMENT ON VIEW seo_agent.v_gsc_page_28d IS '近 28 天 GSC 页面级聚合。';"
    )
    op.execute(
        "COMMENT ON VIEW seo_agent.v_ga4_site_28d IS '近 28 天 GA4 站点级聚合（channel=all）。Dashboard GA4 健康卡直接查这个视图。';"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS seo_agent.v_ga4_site_28d;")
    op.execute("DROP VIEW IF EXISTS seo_agent.v_gsc_page_28d;")
    op.execute("DROP VIEW IF EXISTS seo_agent.v_gsc_query_28d;")
    op.execute("DROP VIEW IF EXISTS seo_agent.v_gsc_site_28d;")
    op.execute("DROP TABLE IF EXISTS seo_agent.ga4_session_daily CASCADE;")
    op.execute("DROP TABLE IF EXISTS seo_agent.gsc_query_daily CASCADE;")
    op.execute("DROP TABLE IF EXISTS seo_agent.google_sync_log CASCADE;")