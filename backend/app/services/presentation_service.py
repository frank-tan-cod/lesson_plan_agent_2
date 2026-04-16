"""Service logic for presentation projects stored in the shared plan table."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from ..presentation_models import PresentationDocument
from ..schemas import PresentationCreate, PresentationUpdate
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .plan_service import PlanService


class PresentationService:
    """CRUD facade for `doc_type="presentation"` projects."""

    DOC_TYPE = "presentation"

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
        self.plan_service = PlanService(db, user_id=self.user_id)

    def create(self, data: PresentationCreate):
        """Create and persist a new presentation project."""
        content = self._normalize_content(data.title, data.content)
        return self.plan_service.create(
            data=self._build_plan_create(
                title=data.title,
                content=content,
                metadata=data.metadata,
            )
        )

    def get(self, plan_id: str):
        """Fetch a presentation project by id."""
        plan = self.plan_service.get(plan_id)
        if plan is None or plan.doc_type != self.DOC_TYPE:
            return None
        return plan

    def list(self, skip: int = 0, limit: int = 100) -> tuple[list[Any], int]:
        """List presentation projects only."""
        return self.plan_service.list(skip=skip, limit=limit, doc_type=self.DOC_TYPE)

    def search(self, query: str, skip: int = 0, limit: int = 100) -> tuple[list[Any], int]:
        """Search presentation projects by title."""
        return self.plan_service.search(query=query, skip=skip, limit=limit, doc_type=self.DOC_TYPE)

    def update(self, plan_id: str, data: PresentationUpdate):
        """Update a presentation project while keeping content/title in sync."""
        plan = self.get(plan_id)
        if plan is None:
            return None

        updates: dict[str, Any] = {}
        next_title = data.title or plan.title
        if data.title is not None:
            updates["title"] = data.title
        if data.metadata is not None:
            updates["metadata"] = data.metadata
        if data.content is not None or data.title is not None:
            source = data.content if data.content is not None else deepcopy(plan.content)
            updates["content"] = self._normalize_content(next_title, source)

        return self.plan_service.update(plan_id, data=self._build_plan_update(updates))

    def delete(self, plan_id: str) -> bool:
        """Delete a presentation project."""
        plan = self.get(plan_id)
        if plan is None:
            return False
        return self.plan_service.delete(plan_id)

    def _normalize_content(self, title: str, content: Any) -> dict[str, Any]:
        """Coerce any supported payload into validated presentation JSON."""
        if hasattr(content, "model_dump"):
            payload = content.model_dump()
        elif isinstance(content, dict):
            payload = deepcopy(content)
        else:
            payload = {"title": title, "slides": []}

        payload["title"] = title
        payload.setdefault("classroom_script", "")
        payload.setdefault("slides", [])
        document = PresentationDocument.model_validate(payload)
        return document.model_dump()

    @staticmethod
    def _build_plan_create(title: str, content: dict[str, Any], metadata: dict[str, Any] | None):
        """Create a shared-plan payload lazily to avoid circular imports."""
        from ..schemas import PlanCreate

        return PlanCreate(title=title, content=content, metadata=metadata, doc_type=PresentationService.DOC_TYPE)

    @staticmethod
    def _build_plan_update(updates: dict[str, Any]):
        """Create a shared-plan update payload lazily to avoid circular imports."""
        from ..schemas import PlanUpdate

        return PlanUpdate(**updates)
