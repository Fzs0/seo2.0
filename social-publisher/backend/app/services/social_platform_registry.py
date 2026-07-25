"""Compatibility facade for the shared social platform contract."""

from exdivo_social_contract import (
    PLATFORMS,
    list_platform_specs,
    platform_spec,
    validate_binding_payload,
    validate_package_payload,
)

__all__ = [
    "PLATFORMS",
    "list_platform_specs",
    "platform_spec",
    "validate_binding_payload",
    "validate_package_payload",
]
