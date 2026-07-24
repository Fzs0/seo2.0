"""enforce the article QA storage contract

Revision ID: b8e2f4c6d901
Revises: a7d1c2e3f4b5
Create Date: 2026-07-23
"""
from __future__ import annotations

from alembic import op


revision = "b8e2f4c6d901"
down_revision = "a7d1c2e3f4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          ADD COLUMN IF NOT EXISTS qa_summary jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN seo_agent.articles.qa_summary IS
          'QA 汇总与兼容元数据；qa_checklist 只保存规范检查项数组。'
        """
    )
    op.execute(
        """
        UPDATE seo_agent.articles AS article
           SET qa_summary = (article.qa_checklist - 'checks')
                            || jsonb_build_object('source_shape', 'legacy_envelope'),
               qa_checklist = COALESCE(
                 (
                   SELECT jsonb_agg(
                            jsonb_build_object('key', entry.key, 'ok', entry.value)
                            || CASE
                                 WHEN article.qa_checklist ? entry.key
                                  AND jsonb_typeof(article.qa_checklist->entry.key) <> 'boolean'
                                 THEN jsonb_build_object(
                                        'value',
                                        article.qa_checklist->entry.key
                                      )
                                 ELSE '{}'::jsonb
                               END
                            ORDER BY entry.key
                          )
                     FROM jsonb_each(article.qa_checklist->'checks') AS entry(key, value)
                 ),
                 '[]'::jsonb
               )
         WHERE jsonb_typeof(article.qa_checklist) = 'object'
           AND jsonb_typeof(article.qa_checklist->'checks') = 'object'
           AND NOT EXISTS (
                 SELECT 1
                   FROM jsonb_each(article.qa_checklist->'checks') AS invalid(key, value)
                  WHERE jsonb_typeof(invalid.value) <> 'boolean'
               )
        """
    )
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          ADD CONSTRAINT articles_qa_summary_is_object
          CHECK (jsonb_typeof(qa_summary) = 'object')
        """
    )
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          ADD CONSTRAINT articles_qa_checklist_is_array
          CHECK (jsonb_typeof(qa_checklist) = 'array') NOT VALID
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          DROP CONSTRAINT IF EXISTS articles_qa_checklist_is_array
        """
    )
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          DROP CONSTRAINT IF EXISTS articles_qa_summary_is_object
        """
    )
    op.execute(
        """
        UPDATE seo_agent.articles AS article
           SET qa_checklist = (article.qa_summary - 'source_shape')
                              || jsonb_build_object(
                                   'checks',
                                   COALESCE(
                                     (
                                       SELECT jsonb_object_agg(
                                                item->>'key',
                                                item->'ok'
                                              )
                                         FROM jsonb_array_elements(article.qa_checklist) AS item
                                     ),
                                     '{}'::jsonb
                                   )
                                 )
         WHERE article.qa_summary->>'source_shape' = 'legacy_envelope'
           AND jsonb_typeof(article.qa_checklist) = 'array'
        """
    )
    op.execute(
        """
        ALTER TABLE seo_agent.articles
          DROP COLUMN IF EXISTS qa_summary
        """
    )
