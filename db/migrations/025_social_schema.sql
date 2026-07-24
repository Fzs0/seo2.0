-- Move social publishing into its own bounded schema without copying data.
-- ALTER TABLE ... SET SCHEMA preserves rows, constraints, indexes and triggers.
CREATE SCHEMA IF NOT EXISTS social;

DO $$
DECLARE
  item record;
BEGIN
  FOR item IN
    SELECT *
      FROM (VALUES
        ('social_decisions', 'decisions'),
        ('social_content_packages', 'content_packages'),
        ('social_content_package_versions', 'content_package_versions'),
        ('social_account_bindings', 'account_bindings'),
        ('social_publish_jobs', 'publish_jobs'),
        ('social_publish_attempts', 'publish_attempts'),
        ('social_posts', 'posts'),
        ('social_metric_snapshots', 'metric_snapshots'),
        ('social_connections', 'connections'),
        ('social_connection_secrets', 'connection_secrets'),
        ('social_connection_runs', 'connection_runs')
      ) AS tables(old_name, new_name)
  LOOP
    IF to_regclass(format('seo_agent.%I', item.old_name)) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE seo_agent.%I SET SCHEMA social', item.old_name);
    END IF;
    IF to_regclass(format('social.%I', item.old_name)) IS NOT NULL
       AND to_regclass(format('social.%I', item.new_name)) IS NULL THEN
      EXECUTE format('ALTER TABLE social.%I RENAME TO %I', item.old_name, item.new_name);
    END IF;
  END LOOP;
END
$$;

COMMENT ON SCHEMA social IS
  'Social content, account bindings, browser delivery jobs and remote publication truth.';
