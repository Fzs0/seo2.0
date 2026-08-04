from app.core.article_urls import canonical_article_connector_type
from app.services.autonomous_strategy_orchestrator import _capability_gate


def test_oemapps_main_role_resolves_to_openapi_publisher_identity() -> None:
    site = {
        "site_type": "main",
        "api_base_url": "https://openapi.oemapps.com",
        "api_config": {},
    }

    assert canonical_article_connector_type(site) == "custom_openapi"


def test_article_plan_uses_verified_capability_instead_of_proposal_role() -> None:
    action = {
        "action_type": "new_article",
        "strategy_type": "new_article",
        "editorial_action": "new_article",
        "connector_type": "main",
        "expected_fields": [],
    }
    site = {"strategy_enabled": True}
    capability = {
        "configuration_issues": [],
        "supported_actions": {"new_article": "approval_required"},
        "supported_fields": {"articles": ["title", "body"]},
        "connectors": {"images": {"status": "unavailable"}},
        "action_adapters": {
            "new_article": {
                "connector_type": "custom_openapi",
                "read": True,
                "write": True,
                "readback": True,
            }
        },
    }

    result = _capability_gate(action, site=site, capability=capability)

    assert result["action_type"] == "new_article"
    assert result["connector_type"] == "custom_openapi"
