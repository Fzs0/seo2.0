-- Strategy review filters use structured values already stored in tasks.decision.
-- Expression indexes keep the common filters cheap without duplicating JSON fields.

CREATE INDEX IF NOT EXISTS tasks_strategy_type_idx
ON seo_agent.tasks ((decision->>'strategy_type'))
WHERE task_type = 'review' AND payload->>'kind' = 'seo_strategy';

CREATE INDEX IF NOT EXISTS tasks_evidence_level_idx
ON seo_agent.tasks ((decision->>'evidence_level'))
WHERE task_type = 'review' AND payload->>'kind' = 'seo_strategy';

CREATE INDEX IF NOT EXISTS tasks_strategy_review_filter_idx
ON seo_agent.tasks (status, priority, site_id, created_at DESC)
WHERE task_type = 'review' AND payload->>'kind' = 'seo_strategy';
