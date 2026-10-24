"""Password hashing (argon2id, ADR-002) and opaque tokens stored only as sha256 (DATA_MODEL sessions)."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# argon2 is CPU-bound (~70 ms, 64 MiB each). It runs on a small dedicated pool so it never blocks the event
# loop, and so concurrent logins cannot take more than 4 x 64 MiB at once.
_ARGON2_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="argon2")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def averify_password(password_hash: str, password: str) -> bool:
    return await asyncio.get_running_loop().run_in_executor(
        _ARGON2_POOL, verify_password, password_hash, password
    )


async def ahash_password(password: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(_ARGON2_POOL, hash_password, password)
