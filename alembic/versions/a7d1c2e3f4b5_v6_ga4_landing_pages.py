"""v6 GA4 landing page daily data."""
from alembic import op

revision = "a7d1c2e3f4b5"
down_revision = "5e9c7b1a4f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ga4_landing_page_daily_site_date_idx ON seo_agent.ga4_landing_page_daily (site_id, date DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS seo_agent.ga4_landing_page_daily CASCADE;")
