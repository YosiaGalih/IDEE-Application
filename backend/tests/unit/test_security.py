"""Password hashing and opaque tokens (ADR-002: argon2id; sessions store only sha256)."""

from __future__ import annotations

import hashlib

from app.core.security import hash_password, hash_token, new_token, verify_password


def test_argon2id_hash_round_trip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong password")


def test_verify_never_raises_on_garbage_hash() -> None:
    assert not verify_password("not-a-hash", "anything")


def test_tokens_are_random_and_only_their_sha256_is_stored() -> None:
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 43
    assert hash_token(a) == hashlib.sha256(a.encode()).hexdigest()
    assert len(hash_token(a)) == 64
