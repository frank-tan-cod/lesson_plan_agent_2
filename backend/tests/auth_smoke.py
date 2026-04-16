"""Smoke test for registration, login, and user-scoped plan isolation."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi import Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _resolve_user_id(token: str) -> str:
    """Resolve a user id through the same auth dependency used by routes."""
    from backend.app.dependencies import get_current_user_id

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    request = Request({"type": "http", "headers": []})
    return get_current_user_id(request=request, credentials=credentials)


async def main() -> None:
    """Run register-login-create-list auth smoke checks."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "auth_smoke.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
        os.environ["JWT_SECRET_KEY"] = "test-secret-key"

        from backend.app.database import init_db, session_maker
        from backend.app.routers.auth import login, register
        from backend.app.routers.plans import create_plan, list_plans
        from backend.app.schemas import LoginRequest, PlanCreate, UserCreate

        init_db()

        with session_maker() as db:
            user_1 = await register(UserCreate(username="alice", password="pass1234"), db)
            user_2 = await register(UserCreate(username="bob", password="pass1234"), db)
            assert user_1.username == "alice"
            assert user_2.username == "bob"

        with session_maker() as db:
            token_1 = (await login(LoginRequest(username="alice", password="pass1234"), Response(), db)).access_token
            token_2 = (await login(LoginRequest(username="bob", password="pass1234"), Response(), db)).access_token

        user_id_1 = _resolve_user_id(token_1)
        user_id_2 = _resolve_user_id(token_2)
        assert user_id_1 != user_id_2

        try:
            _resolve_user_id("invalid-token")
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("Expected invalid token to raise HTTPException(status_code=401).")

        with session_maker() as db:
            created_plan = await create_plan(
                PlanCreate(title="用户一教案", content={"sections": []}),
                db=db,
                user_id=user_id_1,
            )

        with session_maker() as db:
            list_1 = list_plans(db=db, user_id=user_id_1)
            list_2 = list_plans(db=db, user_id=user_id_2)

        assert any(item.id == created_plan.id for item in list_1.items)
        assert all(item.id != created_plan.id for item in list_2.items)

        print("Auth smoke test passed.")
        print(f"Created plan: {created_plan.id}")


if __name__ == "__main__":
    asyncio.run(main())
