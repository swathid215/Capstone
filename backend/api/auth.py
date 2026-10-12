import base64
import hashlib
import hmac
import secrets
from typing import Optional

PASSWORD_ITERATIONS = 310_000
SESSION_BYTES = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_value, digest_value = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_BYTES)


def session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_user(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "phone": row["phone"],
        "email": row["email"],
    }
