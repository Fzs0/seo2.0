"""Encryption seam for connector credentials."""
from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken


class ConnectorSecretError(RuntimeError):
    """Connector secrets cannot be stored or decrypted safely."""


class SecretCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ConnectorSecretError("CONNECTOR_SECRET_KEY is required when connector secrets are saved")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, TypeError) as error:
            raise ConnectorSecretError("CONNECTOR_SECRET_KEY must be a valid Fernet key") from error

    def encrypt(self, values: dict[str, str]) -> bytes:
        clean = {str(key): str(value) for key, value in values.items() if value}
        return self._fernet.encrypt(json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def decrypt(self, value: bytes | memoryview) -> dict[str, str]:
        raw = bytes(value)
        try:
            payload = json.loads(self._fernet.decrypt(raw).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConnectorSecretError("connector secrets could not be decrypted") from error
        if not isinstance(payload, dict):
            raise ConnectorSecretError("connector secret payload is invalid")
        return {str(key): str(item) for key, item in payload.items() if item is not None}


__all__ = ["ConnectorSecretError", "SecretCipher"]
