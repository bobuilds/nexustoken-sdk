from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac

from nexus_sdk import verify_webhook_signature


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_webhook_signature_accepts_current_secret():
    body = b'{"event":"task.settled","task_id":"abc"}'
    secret = "current-secret"

    assert verify_webhook_signature(_signature(body, secret), body, secret)


def test_verify_webhook_signature_accepts_raw_hex_header():
    body = "hello webhook"
    secret = "current-secret"
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()

    assert verify_webhook_signature(digest, body, secret)


def test_verify_webhook_signature_accepts_previous_secret_during_grace_period():
    body = b"rotated body"
    current_secret = "current-secret"
    previous_secret = "previous-secret"
    now = datetime.now(timezone.utc)

    assert verify_webhook_signature(
        _signature(body, previous_secret),
        body,
        current_secret,
        previous_secret=previous_secret,
        previous_secret_expires_at=now + timedelta(minutes=5),
        now=now,
    )


def test_verify_webhook_signature_rejects_previous_secret_after_grace_period():
    body = b"rotated body"
    current_secret = "current-secret"
    previous_secret = "previous-secret"
    now = datetime.now(timezone.utc)

    assert not verify_webhook_signature(
        _signature(body, previous_secret),
        body,
        current_secret,
        previous_secret=previous_secret,
        previous_secret_expires_at=now - timedelta(seconds=1),
        now=now,
    )
