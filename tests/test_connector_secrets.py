from cryptography.fernet import Fernet
import pytest

from app.connectors.secrets import ConnectorSecretError, SecretCipher


def test_secret_cipher_round_trip_and_public_names() -> None:
    cipher = SecretCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt({"api_token": "secret-value", "tenant": "shop-1"})

    assert b"secret-value" not in encrypted
    assert cipher.decrypt(encrypted) == {"api_token": "secret-value", "tenant": "shop-1"}


def test_secret_cipher_requires_valid_fernet_key() -> None:
    with pytest.raises(ConnectorSecretError):
        SecretCipher("")
    with pytest.raises(ConnectorSecretError):
        SecretCipher("not-a-fernet-key")
