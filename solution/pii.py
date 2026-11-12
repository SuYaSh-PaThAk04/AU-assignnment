"""PII at rest: HMAC for phone matching, Fernet for customer names."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet

# Synthetic assignment data only. Override with PII_KEY outside this repo.
_DEFAULT_SECRET = "au-assignment-local-dev-key"


def _secret() -> bytes:
    return os.environ.get("PII_KEY", _DEFAULT_SECRET).encode()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret()).digest())
    return Fernet(key)


def phone_hmac(canonical_phone: str) -> str:
    return hmac.new(_secret(), canonical_phone.encode(), hashlib.sha256).hexdigest()


def encrypt_name(name: str) -> str:
    return _fernet().encrypt(name.encode()).decode()
