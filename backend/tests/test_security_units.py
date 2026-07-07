"""Unit tests for security primitives and the rate limiter."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.exceptions import AuthenticationError
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    decrypt_value,
    encrypt_value,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("CorrectHorse1")
    assert hashed != "CorrectHorse1"
    assert verify_password("CorrectHorse1", hashed)
    assert not verify_password("WrongHorse1", hashed)


def test_password_verify_handles_garbage_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_access_token_roundtrip():
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(user_id, "admin", session_id)
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["sid"] == str(session_id)
    assert payload["role"] == "admin"


def test_expired_access_token_rejected():
    token = create_access_token(
        uuid.uuid4(), "user", uuid.uuid4(), expires_delta=timedelta(seconds=-1)
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_tampered_access_token_rejected():
    token = create_access_token(uuid.uuid4(), "user", uuid.uuid4())
    with pytest.raises(AuthenticationError):
        decode_access_token(token[:-2] + "xx")


def test_refresh_token_hash_is_deterministic_and_opaque():
    digest = hash_refresh_token("some-token")
    assert digest == hash_refresh_token("some-token")
    assert len(digest) == 64
    assert "some-token" not in digest


def test_encryption_roundtrip():
    secret = "sk-super-secret-api-key"
    ciphertext = encrypt_value(secret)
    assert secret not in ciphertext
    assert decrypt_value(ciphertext) == secret


def test_sliding_window_limiter():
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        allowed, _ = limiter.allow("1.2.3.4")
        assert allowed
    allowed, retry_after = limiter.allow("1.2.3.4")
    assert not allowed
    assert retry_after > 0
    # Different clients are isolated from each other.
    allowed, _ = limiter.allow("5.6.7.8")
    assert allowed
