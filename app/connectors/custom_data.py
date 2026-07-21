"""Declarative, read-only JSON connector with a canonical product interface.

The public interface intentionally stays small: validate a configuration, preview a
payload, or build one bounded request. Platform-specific response complexity is
kept behind this module rather than leaking into product or SEO callers.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class ConnectorError(ValueError):
    """Base error for connector validation and execution."""


class ConnectorSecurityError(ConnectorError):
    """Raised when a configuration can escape the read-only HTTP boundary."""


class ConnectorMappingError(ConnectorError):
    """Raised when a response cannot satisfy the declared mapping contract."""


TransformName = Literal[
    "string",
    "integer",
    "decimal",
    "boolean",
    "status",
    "csv",
    "string_list",
    "url",
    "unix_datetime",
    "datetime",
    "images",
    "json",
]

CANONICAL_PRODUCT_FIELDS = {
    "external_id",
    "title",
    "handle",
    "url",
    "canonical_url",
    "description",
    "description_html",
    "meta_title",
    "meta_description",
    "meta_keywords",
    "status",
    "available",
    "price",
    "price_min",
    "price_max",
    "currency",
    "inventory_quantity",
    "image",
    "images",
    "variants",
    "collections",
    "tags",
    "product_type",
    "published_at",
    "source_updated_at",
}

_SECRET_PLACEHOLDER = re.compile(r"^\$\{secret:([A-Za-z][A-Za-z0-9_.-]{0,63})\}$")
_PATH_TOKEN = re.compile(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+|\*)\]")


def _validate_public_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConnectorSecurityError("connector base_url must be a credential-free HTTPS URL")
    host = parsed.hostname.split("%", 1)[0]
    if host.lower() == "localhost":
        raise ConnectorSecurityError("localhost connector targets are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ConnectorSecurityError("private, loopback, reserved, and link-local targets are not allowed")
    return value.rstrip("/")


class RequestSpec(BaseModel):
    method: Literal["GET", "POST"] = "GET"
    base_url: str
    path: str = "/"
    allowed_hosts: list[str] = Field(default_factory=list, max_length=10)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    timeout_seconds: float = Field(default=20, ge=1, le=60)
    max_response_bytes: int = Field(default=3 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    pagination: "PaginationSpec" = Field(default_factory=lambda: PaginationSpec())

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_public_https_url(value.strip())

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip() or "/"
        if not value.startswith("/") or "://" in value or "\\" in value:
            raise ConnectorSecurityError("request path must be an absolute URL path")
        return value

    @model_validator(mode="after")
    def validate_hosts_and_templates(self) -> "RequestSpec":
        base_host = (urlparse(self.base_url).hostname or "").lower()
        hosts = {host.strip().lower() for host in self.allowed_hosts if host.strip()}
        if not hosts:
            self.allowed_hosts = [base_host]
        elif base_host not in hosts:
            raise ConnectorSecurityError("base_url host must be included in allowed_hosts")
        for host in self.allowed_hosts:
            if "://" in host or "/" in host or host.lower() == "localhost":
                raise ConnectorSecurityError("allowed_hosts entries must be hostnames only")
        _validate_secret_templates(self.headers)
        _validate_secret_templates(self.params)
        _validate_secret_templates(self.body or {})
        return self


class PaginationSpec(BaseModel):
    mode: Literal["none", "page"] = "none"
    page_param: str = "page"
    page_size_param: str = "page_size"
    page_size: int = Field(default=100, ge=1, le=500)
    start_page: int = Field(default=1, ge=0)
    current_path: str | None = None
    next_path: str | None = None
    total_pages_path: str | None = None
    max_pages: int = Field(default=10, ge=1, le=50)
    max_items: int = Field(default=2000, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_page_mode(self) -> "PaginationSpec":
        if self.mode == "page" and not (self.next_path or self.total_pages_path):
            raise ValueError("page pagination requires next_path or total_pages_path")
        for value in (self.current_path, self.next_path, self.total_pages_path):
            if value:
                extract_json_path({}, value, missing=None)
        return self


class ResponseSpec(BaseModel):
    success_path: str | None = None
    success_value: str | int | float | bool | None = None
    items_path: str
    pagination_path: str | None = None

    @field_validator("success_path", "items_path", "pagination_path")
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        if value is not None:
            extract_json_path({}, value, missing=None)
        return value


class FieldMapping(BaseModel):
    path: str
    required: bool = False
    transform: TransformName = "json"
    default: Any = None
    base: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        extract_json_path({}, value, missing=None)
        return value

    @field_validator("base")
    @classmethod
    def validate_optional_base(cls, value: str | None) -> str | None:
        return _validate_public_https_url(value.strip()) if value else None


class ConnectorConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    adapter: Literal["custom", "oemapps"] = "custom"
    capability: Literal["products.list"]
    request: RequestSpec
    response: ResponseSpec
    fields: dict[str, FieldMapping]

    @model_validator(mode="after")
    def validate_product_contract(self) -> "ConnectorConfig":
        unknown = set(self.fields) - CANONICAL_PRODUCT_FIELDS
        if unknown:
            raise ValueError(f"unsupported canonical product fields: {sorted(unknown)}")
        missing = {"external_id", "title"} - set(self.fields)
        if missing:
            raise ValueError(f"required canonical mappings missing: {sorted(missing)}")
        for key in ("external_id", "title"):
            if not self.fields[key].required:
                raise ValueError(f"{key} mapping must be required")
        return self


@dataclass(slots=True)
class ConnectorPreview:
    total_items: int
    mapped_items: int
    mapping_errors: int
    items: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    pagination: dict[str, Any] | list[Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_items": self.total_items,
            "mapped_items": self.mapped_items,
            "mapping_errors": self.mapping_errors,
            "items": self.items,
            "errors": self.errors,
            "pagination": self.pagination,
        }


class CustomDataConnector:
    """Map one declared external JSON shape to canonical product records."""

    def __init__(self, config: ConnectorConfig, *, secrets: dict[str, str] | None = None) -> None:
        self.config = config
        self._secrets = {str(key): str(value) for key, value in (secrets or {}).items() if value}

    def public_config(self) -> dict[str, Any]:
        payload = self.config.model_dump(mode="json")
        payload["configured_secret_names"] = sorted(self._secrets)
        return payload

    def build_request(self, *, page: int | None = None) -> dict[str, Any]:
        url = urljoin(f"{self.config.request.base_url}/", self.config.request.path.lstrip("/"))
        host = (urlparse(url).hostname or "").lower()
        if host not in {value.lower() for value in self.config.request.allowed_hosts}:
            raise ConnectorSecurityError("resolved request host is not allowlisted")
        params = _resolve_templates(self.config.request.params, self._secrets)
        pagination = self.config.request.pagination
        if pagination.mode == "page":
            params[pagination.page_param] = pagination.start_page if page is None else page
            params[pagination.page_size_param] = pagination.page_size
        return {
            "method": self.config.request.method,
            "url": url,
            "headers": _resolve_templates(self.config.request.headers, self._secrets),
            "params": params,
            "json": _resolve_templates(self.config.request.body, self._secrets) if self.config.request.body is not None else None,
            "timeout_seconds": self.config.request.timeout_seconds,
            "max_response_bytes": self.config.request.max_response_bytes,
            "allowed_hosts": list(self.config.request.allowed_hosts),
        }

    def next_page(self, payload: dict[str, Any], *, current_page: int) -> int | None:
        pagination = self.config.request.pagination
        if pagination.mode == "none":
            return None
        remote_current = extract_json_path(payload, pagination.current_path, missing=current_page) if pagination.current_path else current_page
        total_pages = extract_json_path(payload, pagination.total_pages_path, missing=None) if pagination.total_pages_path else None
        candidate = extract_json_path(payload, pagination.next_path, missing=None) if pagination.next_path else None
        try:
            current = int(remote_current)
            if total_pages is not None and current >= int(total_pages):
                return None
            next_value = int(candidate) if candidate is not None else current + 1
        except (TypeError, ValueError):
            raise ConnectorMappingError("pagination values must be integers") from None
        return next_value if next_value > current else None

    def preview(
        self,
        payload: dict[str, Any],
        *,
        item_limit: int = 100,
        include_raw: bool = False,
    ) -> ConnectorPreview:
        response = self.config.response
        if response.success_path is not None:
            actual = extract_json_path(payload, response.success_path, missing=None)
            if actual != response.success_value:
                raise ConnectorMappingError(
                    f"response success condition failed: {response.success_path}={actual!r}"
                )
        raw_items = extract_json_path(payload, response.items_path, missing=None)
        if not isinstance(raw_items, list):
            raise ConnectorMappingError(f"items_path {response.items_path!r} did not resolve to an array")
        pagination = (
            extract_json_path(payload, response.pagination_path, missing=None)
            if response.pagination_path
            else None
        )
        mapped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items[: max(1, item_limit)]):
            if not isinstance(raw_item, dict):
                errors.append({"index": index, "field": None, "error": "item is not an object"})
                continue
            try:
                mapped.append(self._map_product(raw_item, include_raw=include_raw))
            except _FieldError as error:
                errors.append({"index": index, "field": error.field, "error": str(error)})
        return ConnectorPreview(
            total_items=len(raw_items),
            mapped_items=len(mapped),
            mapping_errors=len(errors),
            items=mapped,
            errors=errors,
            pagination=pagination if isinstance(pagination, (dict, list)) else None,
        )

    def _map_product(self, raw_item: dict[str, Any], *, include_raw: bool) -> dict[str, Any]:
        product: dict[str, Any] = {}
        for field_name, mapping in self.config.fields.items():
            value = extract_json_path(raw_item, mapping.path, missing=None)
            if value is None or (isinstance(value, str) and not value.strip()):
                value = mapping.default
            if mapping.required and (value is None or value == ""):
                raise _FieldError(field_name, f"required mapping {mapping.path!r} is empty")
            try:
                product[field_name] = _transform(value, mapping)
            except (TypeError, ValueError, InvalidOperation) as error:
                raise _FieldError(field_name, f"could not transform {mapping.path!r}: {error}") from error
        if include_raw:
            product["raw"] = raw_item
        product["seo_audit"] = _seo_audit(product)
        return product


class _FieldError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def extract_json_path(data: Any, path: str, *, missing: Any = None) -> Any:
    """Resolve a safe JSONPath subset: $.key, [index], and [*]."""
    if path == "$":
        return data
    if not isinstance(path, str) or not path.startswith("$."):
        raise ConnectorMappingError("JSONPath must start with '$.'")
    suffix = path[1:]
    tokens: list[str | int] = []
    position = 0
    while position < len(suffix):
        match = _PATH_TOKEN.match(suffix, position)
        if not match:
            raise ConnectorMappingError(f"unsupported JSONPath syntax at {suffix[position:]!r}")
        if match.group(1) is not None:
            tokens.append(match.group(1))
        else:
            raw_index = match.group(2)
            tokens.append("*" if raw_index == "*" else int(raw_index))
        position = match.end()
    current = data
    for token_index, token in enumerate(tokens):
        if token == "*":
            if not isinstance(current, list):
                return missing
            remainder = tokens[token_index + 1 :]
            return [_resolve_tokens(item, remainder, missing) for item in current]
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return missing
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return missing
            current = current[token]
    return current


def _resolve_tokens(data: Any, tokens: list[str | int], missing: Any) -> Any:
    current = data
    for token in tokens:
        if token == "*":
            if not isinstance(current, list):
                return missing
            return [_resolve_tokens(item, tokens[tokens.index(token) + 1 :], missing) for item in current]
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return missing
            current = current[token]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return missing
    return current


def _validate_secret_templates(value: Any) -> None:
    if isinstance(value, dict):
        for nested in value.values():
            _validate_secret_templates(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_secret_templates(nested)
    elif isinstance(value, str) and "${secret:" in value and not _SECRET_PLACEHOLDER.fullmatch(value):
        raise ConnectorSecurityError("secret placeholders must occupy the complete value")


def _resolve_templates(value: Any, secrets: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_templates(nested, secrets) for key, nested in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(nested, secrets) for nested in value]
    if isinstance(value, str):
        match = _SECRET_PLACEHOLDER.fullmatch(value)
        if match:
            secret_name = match.group(1)
            if secret_name not in secrets:
                raise ConnectorSecurityError(f"required secret {secret_name!r} is not configured")
            return secrets[secret_name]
    return value


def _transform(value: Any, mapping: FieldMapping) -> Any:
    if value is None:
        return None
    if mapping.values:
        value = mapping.values.get(str(value), value)
    kind = mapping.transform
    if kind == "json":
        return value
    if kind == "string":
        return str(value).strip()
    if kind == "integer":
        return int(value)
    if kind == "decimal":
        return float(Decimal(str(value)))
    if kind == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "active", "available"}
        return bool(value)
    if kind == "status":
        if str(value).lower() in {"1", "active", "published", "publish", "available"}:
            return "active"
        if str(value).lower() in {"0", "draft", "pending"}:
            return "draft"
        return "archived"
    if kind == "csv":
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [part.strip() for part in str(value).split(",") if part.strip()]
    if kind == "string_list":
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("string_list JSON must be an array")
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    if kind == "url":
        text = str(value).strip()
        return urljoin(f"{mapping.base}/", text.lstrip("/")) if mapping.base else text
    if kind == "unix_datetime":
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    if kind == "datetime":
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        text = str(value).strip()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.isoformat()
    if kind == "images":
        values = value if isinstance(value, list) else [value]
        images: list[dict[str, Any]] = []
        for image in values:
            if isinstance(image, str):
                images.append({"url": image, "alt": ""})
            elif isinstance(image, dict):
                url = image.get("url") or image.get("src")
                if url:
                    images.append({**image, "url": str(url), "alt": str(image.get("alt") or "")})
        return images
    raise ValueError(f"unsupported transform {kind!r}")


def _seo_audit(product: dict[str, Any]) -> dict[str, Any]:
    missing_tdk = [
        field_name
        for field_name in ("title", "meta_title", "meta_description")
        if not str(product.get(field_name) or "").strip()
    ]
    images = product.get("images") or []
    missing_alt = sum(
        1 for image in images if isinstance(image, dict) and not str(image.get("alt") or "").strip()
    )
    return {
        "missing_tdk": missing_tdk,
        "images_total": len(images),
        "images_missing_alt": missing_alt,
        "missing_canonical_url": not bool(product.get("canonical_url") or product.get("url")),
        "missing_description": not bool(product.get("description") or product.get("description_html")),
    }


__all__ = [
    "ConnectorConfig",
    "ConnectorError",
    "ConnectorMappingError",
    "ConnectorPreview",
    "ConnectorSecurityError",
    "CustomDataConnector",
    "extract_json_path",
]
