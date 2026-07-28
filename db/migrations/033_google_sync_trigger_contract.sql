-- Expand the Google sync trigger contract for strategy evidence refreshes.
-- Idempotent for existing databases originally created by migration 005.

ALTER TABLE seo_agent.google_sync_log
  ALTER COLUMN trigger TYPE text;

ALTER TABLE seo_agent.google_sync_log
  DROP CONSTRAINT IF EXISTS google_sync_log_trigger_chk;

ALTER TABLE seo_agent.google_sync_log
  ADD CONSTRAINT google_sync_log_trigger_chk
  CHECK (trigger IN ('manual', 'scheduled', 'retry', 'strategy_hold_refresh'));
