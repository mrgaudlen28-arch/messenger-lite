from __future__ import annotations

import base64
import hashlib
import hmac
import os

ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(derived).decode()}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.b64decode(salt_b64)
        expected_hash = base64.b64decode(hash_b64)

        actual_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )

        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False