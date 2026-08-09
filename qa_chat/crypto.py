"""Authenticated encryption and non-reversible token fingerprints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CipherBox:
    """AES-256-GCM envelope with explicit associated-data domains."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires exactly 32 key bytes")
        self._cipher = AESGCM(key)

    def seal(self, value: str | bytes | dict[str, Any] | list[Any], *, aad: str) -> str:
        if not aad:
            raise ValueError("an encryption domain is required")
        if isinstance(value, bytes):
            raw = value
        elif isinstance(value, str):
            raw = value.encode("utf-8")
        else:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        encrypted = self._cipher.encrypt(nonce, raw, aad.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def open_bytes(self, envelope: str, *, aad: str) -> bytes:
        try:
            raw = base64.urlsafe_b64decode(envelope.encode("ascii"))
        except Exception as exc:
            raise ValueError("invalid encrypted envelope") from exc
        if len(raw) < 29:
            raise ValueError("invalid encrypted envelope")
        try:
            return self._cipher.decrypt(raw[:12], raw[12:], aad.encode("utf-8"))
        except Exception as exc:
            raise ValueError("encrypted envelope authentication failed") from exc

    def open_text(self, envelope: str, *, aad: str) -> str:
        return self.open_bytes(envelope, aad=aad).decode("utf-8")

    def open_json(self, envelope: str, *, aad: str) -> Any:
        return json.loads(self.open_text(envelope, aad=aad))


class TokenHasher:
    """HMAC fingerprints keep low-entropy identifiers out of SQLite."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("token hashing secret must contain at least 32 bytes")
        self._secret = secret

    def digest(self, namespace: str, value: str) -> str:
        if not namespace or not value:
            raise ValueError("namespace and value are required")
        return hmac.new(
            self._secret,
            f"{namespace}\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()
