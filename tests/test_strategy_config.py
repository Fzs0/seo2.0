import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_topic_cluster_cooldown_is_configurable_with_safe_bounds():
    assert Settings(strategy_topic_cooldown_days=14).strategy_topic_cooldown_days == 14
    assert Settings(strategy_topic_cooldown_days=28).strategy_topic_cooldown_days == 28
    with pytest.raises(ValidationError):
        Settings(strategy_topic_cooldown_days=13)
    with pytest.raises(ValidationError):
        Settings(strategy_topic_cooldown_days=29)
