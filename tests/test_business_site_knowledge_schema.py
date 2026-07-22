import inspect

from app.api.v1 import business
from app.api.v1.business import SiteKnowledgeBody
from app.services import keyword_query_service


def test_site_knowledge_body_keeps_generation_policy_for_business_onboarding():
    body = SiteKnowledgeBody(
        status="confirmed",
        services=["installation"],
        verified_assets=[{"url": "https://example.com/service", "facts": ["Fixed-price installation"]}],
        generation_policy={
            "site_role": "local_service",
            "risk_level": "regulated",
            "allowed_sources": [{"url": "https://example.gov/rules"}],
        },
    )

    saved = body.model_dump()

    assert saved["services"] == ["installation"]
    assert saved["verified_assets"][0]["url"] == "https://example.com/service"
    assert saved["generation_policy"]["site_role"] == "local_service"


def test_keyword_list_is_explicitly_scoped_by_business_id():
    """The global business picker must never fall back to a cross-business keyword list."""
    service_signature = inspect.signature(keyword_query_service.list_keywords)
    assert "business_id" in service_signature.parameters
    assert "AND business_id = :business_id" in inspect.getsource(keyword_query_service.list_keywords)

    route_signature = inspect.signature(business.list_keywords_route)
    assert "business_id" in route_signature.parameters
