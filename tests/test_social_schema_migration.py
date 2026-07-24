from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_FILES = (
    ROOT / "app/services/social_connection_service.py",
    ROOT / "app/services/social_delivery_service.py",
    ROOT / "app/services/social_publishing_service.py",
)


def test_social_services_do_not_query_the_seo_schema() -> None:
    for path in SERVICE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "seo_agent.social_" not in source
        assert "social.social_" not in source


def test_social_schema_migration_moves_every_social_table() -> None:
    migration = (ROOT / "db/migrations/025_social_schema.sql").read_text(encoding="utf-8")
    expected = {
        "social_decisions": "decisions",
        "social_content_packages": "content_packages",
        "social_content_package_versions": "content_package_versions",
        "social_account_bindings": "account_bindings",
        "social_publish_jobs": "publish_jobs",
        "social_publish_attempts": "publish_attempts",
        "social_posts": "posts",
        "social_metric_snapshots": "metric_snapshots",
        "social_connections": "connections",
        "social_connection_secrets": "connection_secrets",
        "social_connection_runs": "connection_runs",
    }
    assert "ALTER TABLE seo_agent.%I SET SCHEMA social" in migration
    for old_name, new_name in expected.items():
        assert f"('{old_name}', '{new_name}')" in migration
