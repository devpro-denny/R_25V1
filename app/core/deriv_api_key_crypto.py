"""
Encryption helpers for Deriv API keys stored in Supabase.
"""

import base64
import hashlib
import logging
from typing import Optional

from app.core.settings import settings

logger = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:v1:"

_fernet_instance = None


def _get_fernet():
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError(
            "cryptography package is required for DERIV API key encryption. "
            "Install dependencies from requirements.txt."
        ) from exc

    secret = settings.DERIV_API_KEY_ENCRYPTION_SECRET
    key_bytes = hashlib.sha256(secret.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    _fernet_instance = Fernet(fernet_key)
    return _fernet_instance


def is_encrypted_deriv_api_key(value: Optional[str]) -> bool:
    return bool(value and value.startswith(ENCRYPTED_PREFIX))


def encrypt_deriv_api_key(plaintext_key: Optional[str]) -> Optional[str]:
    """
    Encrypt a Deriv API key before storing in Supabase.
    Returns the encrypted payload with a stable prefix.
    """
    if plaintext_key is None:
        return None

    if is_encrypted_deriv_api_key(plaintext_key):
        return plaintext_key

    token = _get_fernet().encrypt(plaintext_key.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_deriv_api_key(stored_value: Optional[str]) -> Optional[str]:
    """
    Decrypt an encrypted Deriv API key from Supabase.
    If value is plaintext (legacy), returns as-is.
    """
    if not stored_value:
        return stored_value

    if not is_encrypted_deriv_api_key(stored_value):
        logger.warning("Plaintext Deriv API key detected in Supabase profile")
        return stored_value

    encrypted_payload = stored_value[len(ENCRYPTED_PREFIX) :]
    plaintext = _get_fernet().decrypt(encrypted_payload.encode("utf-8"))
    return plaintext.decode("utf-8")
