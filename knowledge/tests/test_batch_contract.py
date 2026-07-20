from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from knowledge.backend.app.schemas import BatchSourceSpec


def _spec(**overrides) -> BatchSourceSpec:
    return BatchSourceSpec(
        seed_url="https://blog.example.test/",
        source_name="Example blog",
        rights_confirmed=True,
        **overrides,
    )


def test_batch_source_date_cutoffs_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        _spec(years=2, date_from=datetime(2025, 1, 1, tzinfo=UTC))


def test_batch_source_defaults_exclude_unknown_dates_and_bound_work() -> None:
    spec = _spec()

    assert spec.discovery_mode == "auto"
    assert spec.include_unknown_dates is False
    assert spec.max_articles == 100

    with pytest.raises(ValidationError):
        _spec(max_articles=5001)
