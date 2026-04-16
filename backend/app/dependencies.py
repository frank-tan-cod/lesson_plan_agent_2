"""Dependency helpers for editor-related services."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.tools import ToolExecutor, default_registry
from backend.tools.registry import ToolsRegistry

from .core.security import AUTH_COOKIE_NAME, SecurityConfigurationError, decode_token
from .database import get_db, session_maker
from .models import User
from .services import ConversationService, KnowledgeService, OperationService, PlanService
from .tools import register_conversation_tools, register_knowledge_tools, register_presentation_tools, register_web_tools
from .tools.lesson_tools import register_lesson_tools
from .user_context import DEFAULT_USER_ID, resolve_user_id

security = HTTPBearer(auto_error=False)


def get_tools_registry() -> ToolsRegistry:
    """Return the shared tools registry with default tools loaded."""
    register_lesson_tools(default_registry)
    register_knowledge_tools(default_registry)
    return register_conversation_tools(default_registry)


def get_tool_executor() -> ToolExecutor:
    """Create a tool executor for the shared registry."""
    return ToolExecutor(get_tools_registry())


def get_presentation_tools_registry() -> ToolsRegistry:
    """Return a dedicated registry for presentation editing."""
    registry = ToolsRegistry()
    register_presentation_tools(registry)
    register_knowledge_tools(registry)
    register_web_tools(registry)
    return register_conversation_tools(registry)


def get_plan_service(db: Session, user_id: str | None = None) -> PlanService:
    """Create a plan service for the given session."""
    return PlanService(db, user_id=resolve_user_id(user_id, DEFAULT_USER_ID))


def get_conversation_service(db: Session, user_id: str | None = None) -> ConversationService:
    """Create a conversation service for the given session."""
    return ConversationService(db, user_id=resolve_user_id(user_id, DEFAULT_USER_ID))


def get_operation_service(db: Session, user_id: str | None = None) -> OperationService:
    """Create an operation service for the given session."""
    return OperationService(db, user_id=resolve_user_id(user_id, DEFAULT_USER_ID))


def get_knowledge_service(db: Session, user_id: str | None = None) -> KnowledgeService:
    """Create a knowledge service for the given session."""
    return KnowledgeService(db, user_id=resolve_user_id(user_id, DEFAULT_USER_ID))


def get_session_factory():
    """Return the shared session factory used for thread offloading."""
    return session_maker


def get_current_user_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Resolve the current user id from either a bearer token or auth cookie."""
    token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    else:
        token = request.cookies.get(AUTH_COOKIE_NAME)

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token.")

    try:
        user_id = decode_token(token)
    except SecurityConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证配置缺失，服务暂不可用。",
        ) from exc
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    return user_id


def get_current_user(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> User:
    """Load the authenticated user record."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    return user
