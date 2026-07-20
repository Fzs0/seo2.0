-- SEO Workbench Migration 016: standalone knowledge Claim records
--
-- This table intentionally contains Claim records only. The source knowledge
-- database's documents, sources, evidence and usages are not copied here.
-- origin_document_id is retained as an opaque provenance identifier only; it
-- has no foreign key because the document table is not being migrated.

BEGIN;

CREATE TABLE IF NOT EXISTS seo_agent.knowledge_claims (
  id                          uuid PRIMARY KEY,
  origin_document_id          uuid NOT NULL,
  channel                     text NOT NULL,
  topic                       text,
  knowledge_type              text NOT NULL DEFAULT 'article_insight',
  statement                   text NOT NULL,
  conditions                  jsonb NOT NULL DEFAULT '[]'::jsonb,
  exceptions                  jsonb NOT NULL DEFAULT '[]'::jsonb,
  recommended_action          text,
  confidence                  numeric(4,3) NOT NULL DEFAULT 0.550
    CHECK (confidence >= 0 AND confidence <= 1),
  review_status               text NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending', 'approved', 'rejected')),
  review_note                 text,
  reviewed_by                 text,
  reviewed_at                 timestamptz,
  metadata                    jsonb NOT NULL DEFAULT '{}'::jsonb,
  quality_status              text NOT NULL DEFAULT 'unreviewed'
    CHECK (quality_status IN ('unreviewed', 'keep', 'reject', 'uncertain', 'error')),
  quality_utility_score       numeric(4,3)
    CHECK (quality_utility_score IS NULL OR quality_utility_score BETWEEN 0 AND 1),
  quality_reviewer_confidence numeric(4,3)
    CHECK (quality_reviewer_confidence IS NULL OR quality_reviewer_confidence BETWEEN 0 AND 1),
  quality_reasons             jsonb NOT NULL DEFAULT '[]'::jsonb,
  quality_note                text,
  quality_model               text,
  quality_prompt_version      text,
  quality_reviewed_at         timestamptz,
  search_vector               tsvector GENERATED ALWAYS AS (
    to_tsvector(
      'simple',
      coalesce(topic, '') || ' ' || coalesce(statement, '') || ' ' || coalesce(recommended_action, '')
    )
  ) STORED,
  created_at                  timestamptz NOT NULL,
  updated_at                  timestamptz NOT NULL,
  CONSTRAINT knowledge_claims_channel_chk CHECK (length(btrim(channel)) > 0),
  CONSTRAINT knowledge_claims_statement_chk CHECK (length(btrim(statement)) > 0)
);

CREATE INDEX IF NOT EXISTS knowledge_claims_review_channel_idx
  ON seo_agent.knowledge_claims (review_status, channel, created_at DESC);

CREATE INDEX IF NOT EXISTS knowledge_claims_quality_idx
  ON seo_agent.knowledge_claims (quality_status, review_status, created_at DESC);

CREATE INDEX IF NOT EXISTS knowledge_claims_search_idx
  ON seo_agent.knowledge_claims USING gin (search_vector);

COMMENT ON TABLE seo_agent.knowledge_claims IS
  'Migrated Claim-only records from the standalone knowledge service; source, document, evidence and usage rows are intentionally not copied.';
COMMENT ON COLUMN seo_agent.knowledge_claims.origin_document_id IS
  'Opaque ID of the source knowledge document, retained for provenance only; no local document FK exists.';
COMMENT ON COLUMN seo_agent.knowledge_claims.review_status IS
  'Original human review state. Only approved records are eligible for production consumption.';

COMMIT;
