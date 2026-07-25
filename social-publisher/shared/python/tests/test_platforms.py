from __future__ import annotations

import pytest

from exdivo_social_contract import (
    PLATFORMS,
    list_platform_specs,
    platform_spec,
    validate_binding_payload,
    validate_connection_config,
    validate_connection_secrets,
)


EXPECTED_PLATFORMS = {
    "x", "reddit", "quora", "tiktok", "youtube", "instagram", "facebook",
}


def test_all_platforms_have_one_complete_connection_contract() -> None:
    platforms = {item["platform"] for item in list_platform_specs()}
    assert platforms == EXPECTED_PLATFORMS
    for platform in platforms:
        assert validate_connection_config(platform, {}) == {
            "base_url": "http://127.0.0.1:6873",
        }
        validate_connection_secrets(platform, {})


def test_unknown_platforms_are_domain_validation_errors() -> None:
    with pytest.raises(ValueError, match="unsupported social platform"):
        validate_connection_config("myspace", {})
    with pytest.raises(ValueError, match="unsupported social platform"):
        validate_connection_secrets("myspace", {})


def test_public_specs_are_defensive_copies() -> None:
    spec = platform_spec("x")
    spec["allowed_hosts"].append("malicious.example")
    assert "malicious.example" not in platform_spec("x")["allowed_hosts"]


def test_legacy_platforms_dict_cannot_mutate_canonical_policy() -> None:
    PLATFORMS["x"]["allowed_hosts"].append("malicious.example")
    try:
        assert "malicious.example" not in platform_spec("x")["allowed_hosts"]
        with pytest.raises(ValueError, match="host is not allowed"):
            validate_binding_payload(
                {
                    "platform": "x",
                    "delivery_channel": "hubstudio_browser",
                    "publish_adapter": "x_web_intent",
                    "default_publish_url": "https://malicious.example/publish",
                    "enabled": False,
                    "container_code": "container-1",
                }
            )
    finally:
        PLATFORMS["x"]["allowed_hosts"].remove("malicious.example")
