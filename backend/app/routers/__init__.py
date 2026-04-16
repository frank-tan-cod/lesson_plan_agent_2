"""Router exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "auth_router",
    "plans_router",
    "presentations_router",
    "images_router",
    "conversations_router",
    "operations_router",
    "savepoints_router",
    "editor_router",
    "presentation_editor_router",
    "export_router",
    "knowledge_router",
    "preferences_router",
    "temp_preferences_router",
    "user_router",
]


def __getattr__(name: str) -> Any:
    """Lazily import routers so optional dependencies stay isolated."""
    module_map = {
        "auth_router": ".auth",
        "plans_router": ".plans",
        "presentations_router": ".presentations",
        "images_router": ".images",
        "conversations_router": ".conversations",
        "operations_router": ".operations",
        "savepoints_router": ".savepoints",
        "editor_router": ".editor",
        "presentation_editor_router": ".presentation_editor",
        "export_router": ".export",
        "knowledge_router": ".knowledge",
        "preferences_router": ".preferences",
        "temp_preferences_router": ".temp_preferences",
        "user_router": ".user",
    }
    module_name = module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, __name__)
    return getattr(module, "router")
