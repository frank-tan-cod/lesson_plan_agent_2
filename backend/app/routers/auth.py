"""Authentication routes for user registration and login."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.security import (
    SecurityConfigurationError,
    clear_auth_cookie,
    create_access_token,
    get_password_hash,
    set_auth_cookie,
    verify_password,
)
from ..database import get_db
from ..models import User
from ..schemas import LoginRequest, Token, UserCreate, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    """Create a new user account."""
    existing = db.execute(select(User).where(User.username == user_data.username.strip())).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")

    user = User(
        username=user_data.username.strip(),
        password_hash=get_password_hash(user_data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/login", response_model=Token)
async def login(login_data: LoginRequest, response: Response, db: Session = Depends(get_db)) -> Token:
    """Authenticate a user and return a bearer token."""
    user = db.execute(select(User).where(User.username == login_data.username.strip())).scalar_one_or_none()
    if user is None or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")

    try:
        token = create_access_token({"sub": user.id})
    except SecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证配置缺失，暂时无法登录。",
        ) from exc
    set_auth_cookie(response, token)
    return Token(access_token=token, token_type="bearer")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    """Clear the current authentication cookie."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookie(response)
    return response
