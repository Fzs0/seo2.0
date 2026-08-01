"""Delivery-boundary checks for the strategy backend feature.

These tests deliberately exercise import and file boundaries only. Git staging
is verified separately at delivery time because a unit test must not mutate the
repository index.
"""

from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SERVICE_MODULES = (
    "app.services.strategy_evidence_refresh_service",
    "app.services.strategy_hold_service",
    "app.services.strategy_on_page_execution",
)

REQUIRED_MIGRATIONS = (
    "db/migrations/031_invalidate_late_strategy_effect_baselines.sql",
    "db/migrations/032_classify_legacy_new_article_zero_baselines.sql",
)

REQUIRED_FEATURE_TESTS = (
    "tests/test_strategy_evidence_refresh_service.py",
    "tests/test_strategy_hold_service.py",
    "tests/test_strategy_on_page_execution.py",
    "tests/test_strategy_effect_migrations_postgres.py",
)


def test_strategy_delivery_modules_import_from_repository():
    imported = [importlib.import_module(module) for module in REQUIRED_SERVICE_MODULES]

    assert [module.__name__ for module in imported] == list(REQUIRED_SERVICE_MODULES)


def test_strategy_delivery_contains_migrations_and_corresponding_tests():
    missing = [
        relative_path
        for relative_path in (*REQUIRED_MIGRATIONS, *REQUIRED_FEATURE_TESTS)
        if not (ROOT / relative_path).is_file()
    ]

    assert missing == []


def test_runtime_and_test_trees_do_not_contain_delivery_artifacts():
    forbidden_suffixes = {
        ".avi",
        ".gif",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp4",
        ".png",
        ".webm",
    }
    forbidden_names = {
        ".codex-video-frames",
        ".codex_tmp_inspect_instagram.js",
        ".codex_tmp_video_frames.py",
    }

    offenders = []
    for relative_root in ("app", "db", "tests"):
        for path in (ROOT / relative_root).rglob("*"):
            if path.name in forbidden_names or (
                path.is_file() and path.suffix.lower() in forbidden_suffixes
            ):
                offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []
