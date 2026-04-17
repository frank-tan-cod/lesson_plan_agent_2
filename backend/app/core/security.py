"""Password hashing and JWT helpers."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Literal

from dotenv import load_dotenv
from fastapi import Response
from jose import JWTError, jwt
from passlib.context import CryptContext

load_dotenv()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
AUTH_COOKIE_NAME = "lesson_plan_agent_session"
INSECURE_JWT_SECRET_KEYS = {
    "",
    "your-secret-key-change-in-production",
    "change-me-to-a-long-random-string",
}


class SecurityConfigurationError(RuntimeError):
    """Raised when authentication-related runtime settings are invalid."""


def get_secret_key() -> str:
    """Return the configured JWT secret and reject empty/example placeholders."""
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if secret in INSECURE_JWT_SECRET_KEYS:
        raise SecurityConfigurationError("JWT_SECRET_KEY 未配置或仍为示例默认值，请先设置安全密钥。")
    return secret


def validate_security_configuration() -> None:
    """Fail fast when authentication settings are not safe to serve with."""
    get_secret_key()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plaintext password."""
    return pwd_context.hash(password)


def create_access_token(data: dict[str, str], expires_delta: timedelta | None = None) -> str:
    """Encode a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    """Decode a token and extract the subject claim."""
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
    except JWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) and subject.strip() else None


def get_auth_cookie_secure() -> bool:
    """Return whether auth cookies should require HTTPS transport."""
    return os.getenv("AUTH_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}


def get_auth_cookie_samesite() -> Literal["lax", "strict", "none"]:
    """Return the SameSite policy for auth cookies."""
    raw_value = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if raw_value in {"lax", "strict", "none"}:
        return raw_value
    return "lax"


def set_auth_cookie(response: Response, token: str) -> None:
    """Persist the signed JWT in an httpOnly cookie."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=get_auth_cookie_secure(),
        samesite=get_auth_cookie_samesite(),
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Expire the auth cookie on the client."""
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        secure=get_auth_cookie_secure(),
        samesite=get_auth_cookie_samesite(),
        path="/",
    )
