"""PBKDF2 password hashing for dashboard users (no extra dependencies)."""

from __future__ import annotations

import hashlib
import secrets

_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _ITERATIONS,
    )
    return f"{_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    parts = stored.split("$", 2)
    if len(parts) != 3:
        return False
    try:
        iterations = int(parts[0])
        salt_hex, hash_hex = parts[1], parts[2]
    except ValueError:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    try:
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    return secrets.compare_digest(dk, expected)
