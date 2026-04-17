"""FastAPI application entrypoint for the lesson plan backend."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path


def _configure_local_dependency_path() -> None:
    """Prefer the repo-local dependency bundle when present."""
    repo_root = Path(__file__).resolve().parent
    local_deps = repo_root / ".pydeps"

    if not local_deps.exists():
        return

    local_deps_str = str(local_deps)
    if local_deps_str not in sys.path:
        sys.path.insert(0, local_deps_str)


_configure_local_dependency_path()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.dependencies import get_tools_registry
from backend.app.core.security import validate_security_configuration
from backend.app.core.settings import settings
from backend.app.database import init_db
from backend.app.routers import (
    auth_router,
    conversations_router,
    editor_router,
    export_router,
    images_router,
    knowledge_router,
    operations_router,
    plans_router,
    presentation_editor_router,
    presentations_router,
    preferences_router,
    savepoints_router,
    temp_preferences_router,
    user_router,
)
from backend.app.services.knowledge_service import initialize_knowledge_resources


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize application resources on startup."""
    validate_security_configuration()
    init_db()
    get_tools_registry()
    initialize_knowledge_resources()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Lesson Plan Agent Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.CORS_ALLOW_ORIGINS),
        allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    uploads_dir = Path(__file__).resolve().parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")
    app.include_router(auth_router)
    app.include_router(plans_router)
    app.include_router(presentations_router)
    app.include_router(images_router)
    app.include_router(conversations_router)
    app.include_router(operations_router)
    app.include_router(savepoints_router)
    app.include_router(editor_router)
    app.include_router(presentation_editor_router)
    app.include_router(export_router)
    app.include_router(knowledge_router)
    app.include_router(preferences_router)
    app.include_router(temp_preferences_router)
    app.include_router(user_router)
    return app


app = create_app()
