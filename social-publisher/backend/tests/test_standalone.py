from pathlib import Path

from app.clients.social_executor import SocialExecutorClient, SocialExecutorError
from app.connectors.social_connections import HubstudioConnection, SocialConnectionError
from app.main import app
from app.services.social_connection_service import _validate_config
from app.services.social_platform_registry import list_platform_specs


def test_standalone_interface_contains_publish_and_pairing_routes() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/health" in paths
    assert "/api/v1/social/publish-jobs/confirm-batch" in paths
    assert "/api/v1/social/extension/pairing-codes" in paths


def test_all_required_platforms_are_registered() -> None:
    assert list_platform_specs.__module__ == "exdivo_social_contract.platforms"
    assert {item["platform"] for item in list_platform_specs()} == {
        "x", "reddit", "quora", "youtube", "tiktok", "facebook", "instagram",
    }
    for platform in {
        "x", "reddit", "quora", "youtube", "tiktok", "facebook", "instagram",
    }:
        assert _validate_config(platform, {}) == {
            "base_url": "http://127.0.0.1:6873"
        }


def test_local_adapters_reject_remote_control_addresses() -> None:
    try:
        HubstudioConnection(base_url="http://192.168.1.10:6873")
    except SocialConnectionError:
        pass
    else:
        raise AssertionError("Hubstudio adapter accepted a remote address")

    try:
        SocialExecutorClient(
            base_url="http://192.168.1.10:4317",
            shared_secret="x" * 32,
        )
    except SocialExecutorError:
        pass
    else:
        raise AssertionError("executor client accepted a remote address")


def test_schema_is_self_contained() -> None:
    schema = (
        Path(__file__).resolve().parents[2] / "database" / "schema.sql"
    ).read_text(encoding="utf-8")
    assert "seo_agent." not in schema
    assert "CREATE TABLE IF NOT EXISTS social.publish_jobs" in schema
