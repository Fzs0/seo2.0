BEGIN;

DO $$
DECLARE
  current_definition text;
BEGIN
  SELECT indexdef
    INTO current_definition
    FROM pg_indexes
   WHERE schemaname = 'seo_agent'
     AND indexname = 'keywords_unique_import_scope';

  IF current_definition IS NOT NULL AND position('business_id' IN current_definition) = 0 THEN
    EXECUTE 'DROP INDEX seo_agent.keywords_unique_import_scope';
  END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS keywords_unique_import_scope
ON seo_agent.keywords (
  coalesce(business_id, ''),
  normalized_keyword,
  coalesce(semrush_database, ''),
  coalesce(market, ''),
  coalesce(language_code, '')
);

COMMIT;
