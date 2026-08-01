from __future__ import annotations

import inspect

from app.services import strategy_service
from app.services.strategy_service import _strategy_row


def test_strategy_service_is_read_only_history_module():
    public_functions = {
        name
        for name, value in vars(strategy_service).items()
        if inspect.iscoroutinefunction(value) and not name.startswith("_")
    }
    assert public_functions == {"list_strategy_candidates", "list_strategies"}
    source = inspect.getsource(strategy_service)
    assert "generate_strategies" not in source
    assert "_replace_strategy_plan" not in source
    assert "strategy_candidate" in source
    assert "INSERT INTO" not in source
    assert "UPDATE seo_agent" not in source


def test_strategy_row_preserves_ai_proposal_lineage_without_candidate():
    row = {
        "id": "strategy-1",
        "status": "queued",
        "decision": {
            "strategy_type": "new_article",
            "option_origin": "ai_proposed_action",
            "option_id": "proposal-1",
        },
    }
    result = _strategy_row(row)
    assert result["status"] == "pending"
    assert result["option_origin"] == "ai_proposed_action"
    assert result["option_id"] == "proposal-1"
    assert "candidate_id" not in result
