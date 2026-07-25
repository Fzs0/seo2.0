"""Stable social publishing contracts shared by both backend runtimes."""

from .platforms import (
    PLATFORMS,
    list_platform_specs,
    platform_spec,
    validate_binding_payload,
    validate_connection_config,
    validate_connection_secrets,
    validate_package_payload,
)

__all__ = [
    "PLATFORMS",
    "list_platform_specs",
    "platform_spec",
    "validate_binding_payload",
    "validate_connection_config",
    "validate_connection_secrets",
    "validate_package_payload",
]
