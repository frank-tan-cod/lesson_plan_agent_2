from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi import Response
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.security import (
    AUTH_COOKIE_NAME,
    SecurityConfigurationError,
    clear_auth_cookie,
    create_access_token,
    get_password_hash,
    get_secret_key,
    set_auth_cookie,
    validate_security_configuration,
)
from backend.app.dependencies import get_current_user_id
from backend.app.routers.auth import login
from backend.app.schemas import LoginRequest


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeLoginDB:
    def __init__(self, user: object) -> None:
        self._user = user

    def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(self._user)


def test_get_secret_key_rejects_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        get_secret_key()


def test_get_secret_key_rejects_example_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "change-me-to-a-long-random-string")

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        get_secret_key()


def test_get_secret_key_accepts_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")

    assert get_secret_key() == "test-secret-key"


def test_validate_security_configuration_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    with pytest.raises(SecurityConfigurationError, match="JWT_SECRET_KEY"):
        validate_security_configuration()


def test_set_auth_cookie_uses_httponly_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)

    response = Response()
    set_auth_cookie(response, "signed-token")

    cookie_header = response.headers["set-cookie"]
    assert f"{AUTH_COOKIE_NAME}=signed-token" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header


def test_clear_auth_cookie_expires_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SAMESITE", raising=False)

    response = Response()
    clear_auth_cookie(response)

    cookie_header = response.headers["set-cookie"]
    assert f"{AUTH_COOKIE_NAME}=" in cookie_header
    assert "Max-Age=0" in cookie_header


def test_get_current_user_id_accepts_auth_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key")
    token = create_access_token({"sub": "user-123"})
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{AUTH_COOKIE_NAME}={token}".encode())],
        }
    )

    assert get_current_user_id(request=request, credentials=None) == "user-123"


def test_get_current_user_id_returns_503_when_security_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{AUTH_COOKIE_NAME}=invalid-token".encode())],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user_id(request=request, credentials=None)

    assert exc_info.value.status_code == 503
    assert "认证配置缺失" in exc_info.value.detail


@pytest.mark.anyio
async def test_login_returns_503_when_security_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    user = SimpleNamespace(id="user-1", username="alice", password_hash=get_password_hash("pass1234"))

    with pytest.raises(HTTPException) as exc_info:
        await login(
            LoginRequest(username="alice", password="pass1234"),
            Response(),
            _FakeLoginDB(user),
        )

    assert exc_info.value.status_code == 503
    assert "认证配置缺失" in exc_info.value.detail


def test_auth_routes_are_async() -> None:
    from backend.app.routers.auth import logout, register

    assert inspect.iscoroutinefunction(register)
    assert inspect.iscoroutinefunction(login)
    assert inspect.iscoroutinefunction(logout)
