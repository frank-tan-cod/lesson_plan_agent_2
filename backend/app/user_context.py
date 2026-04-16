"""Helpers for normalizing user-context inputs across app entrypoints."""

from __future__ import annotations

from typing import Any

DEFAULT_USER_ID = "default"


def resolve_user_id(user_id: Any, default_user_id: str = DEFAULT_USER_ID) -> str:
    """Normalize user identifiers passed from FastAPI or direct function calls."""
    resolved_default = _normalize_default_user_id(default_user_id)
    if isinstance(user_id, str):
        normalized = user_id.strip()
        return normalized or resolved_default
    if user_id is None or _is_fastapi_depends_marker(user_id):
        return resolved_default

    normalized = str(user_id).strip()
    return normalized or resolved_default


def _normalize_default_user_id(default_user_id: Any) -> str:
    if isinstance(default_user_id, str):
        normalized = default_user_id.strip()
        return normalized or DEFAULT_USER_ID
    if default_user_id is None or _is_fastapi_depends_marker(default_user_id):
        return DEFAULT_USER_ID

    normalized = str(default_user_id).strip()
    return normalized or DEFAULT_USER_ID


def _is_fastapi_depends_marker(value: Any) -> bool:
    cls = value.__class__
    return cls.__module__ == "fastapi.params" and cls.__name__ == "Depends"
