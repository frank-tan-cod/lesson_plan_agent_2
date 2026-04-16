"""Service layer exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ConversationService",
    "DocumentEditor",
    "ExportService",
    "KnowledgeService",
    "OperationService",
    "PreferenceService",
    "PlanService",
    "PresentationEditor",
    "PresentationExportService",
    "PresentationService",
    "SavepointService",
]


def __getattr__(name: str) -> Any:
    """Lazily import services to avoid loading optional dependencies too early."""
    module_map = {
        "ConversationService": ".conversation_service",
        "DocumentEditor": ".editor_service",
        "ExportService": ".export_service",
        "KnowledgeService": ".knowledge_service",
        "OperationService": ".operation_service",
        "PreferenceService": ".preference_service",
        "PlanService": ".plan_service",
        "PresentationEditor": ".presentation_editor_service",
        "PresentationExportService": ".export_pptx",
        "PresentationService": ".presentation_service",
        "SavepointService": ".savepoint_service",
    }
    module_name = module_map.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, __name__)
    return getattr(module, name)
