"""
Webhook signature verification helpers for demand-side SDK users.

The server signs webhook bodies via the `X-Nexus-Signature` header using:
    sha256=<hex digest>

This module provides a small, dependency-free helper to verify that signature
against the raw request body and one or two secrets.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone


def _to_bytes(value: str | bytes | bytearray | memoryview, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"{name} must be str or bytes-like, got {type(value)!r}")


def _normalize_signature(signature: str | bytes) -> str:
    sig = _to_bytes(signature, "signature").decode("utf-8").strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    return sig.lower()


def _digest(body: bytes, secret: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _is_grace_period_active(
    previous_secret_expires_at: datetime | None,
    now: datetime | None,
) -> bool:
    if previous_secret_expires_at is None:
        return False
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    expires_at = previous_secret_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return current_time <= expires_at


def verify_webhook_signature(
    signature: str | bytes,
    body: str | bytes | bytearray | memoryview,
    current_secret: str | bytes,
    previous_secret: str | bytes | None = None,
    previous_secret_expires_at: datetime | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """
    Verify a Nexus webhook signature.

    Args:
        signature: The `X-Nexus-Signature` header value, with or without the `sha256=` prefix.
        body: Raw webhook body bytes or text.
        current_secret: The current webhook signing secret.
        previous_secret: Optional grace-period secret for rotated accounts.
        previous_secret_expires_at: When the previous secret stops being accepted.
        now: Optional timestamp override for tests.

    Returns:
        True when the signature matches the current secret or an active previous secret.
    """
    if not signature or not current_secret:
        return False

    body_bytes = _to_bytes(body, "body")
    expected = _normalize_signature(signature)

    secrets: list[str | bytes] = [current_secret]
    if previous_secret and _is_grace_period_active(previous_secret_expires_at, now):
        secrets.append(previous_secret)

    for secret in secrets:
        if hmac.compare_digest(expected, _digest(body_bytes, _to_bytes(secret, "secret"))):
            return True
    return False
