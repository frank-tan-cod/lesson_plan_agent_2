"""Service logic for lesson plan CRUD operations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import Plan
from ..schemas import PlanCreate, PlanUpdate
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .knowledge_service import KnowledgeService
from .plan_generator import PlanGenerationError, generate_plan_from_requirements
from .reference_context import build_reference_context


class PlanService:
    """Encapsulates lesson plan persistence logic."""

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)

    def create(self, data: PlanCreate) -> Plan:
        """Create and persist a new lesson plan."""
        metadata = dict(data.metadata or {})
        doc_type = data.doc_type or "lesson"
        additional_files = [str(item).strip() for item in data.additional_files if str(item).strip()]
        course_context = (data.course_context or "").strip() or None
        if additional_files:
            metadata["creation_additional_file_ids"] = additional_files
        if course_context:
            metadata["creation_course_context"] = course_context
        if data.content is not None:
            content = data.content
        elif doc_type == "presentation":
            content = {"title": data.title, "classroom_script": "", "slides": []}
        else:
            content = {"sections": []}

        if data.requirements and doc_type == "lesson":
            extra_context = build_reference_context(
                knowledge_service=KnowledgeService(self.db, user_id=self.user_id),
                additional_file_ids=additional_files,
                user_id=self.user_id,
            )
            try:
                generated_content = generate_plan_from_requirements(
                    title=data.title,
                    subject=data.subject,
                    grade=data.grade,
                    requirements=data.requirements,
                    extra_context=extra_context,
                    course_context=course_context,
                )
            except PlanGenerationError:
                raise
            generated_metadata = generated_content.get("metadata", {})
            if isinstance(generated_metadata, dict):
                metadata = {**generated_metadata, **metadata}
            content = {
                **generated_content,
                "metadata": metadata,
            }

        plan = Plan(
            user_id=self.user_id,
            title=data.title,
            doc_type=doc_type,
            subject=data.subject,
            grade=data.grade,
            content=content,
            metadata_json=metadata,
        )
        self.db.add(plan)
        try:
            self.db.commit()
            self.db.refresh(plan)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to create plan.") from exc
        return plan

    def get(self, plan_id: str) -> Plan | None:
        """Fetch a single lesson plan by id."""
        return self.db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.user_id == self.user_id)
        ).scalar_one_or_none()

    def list(
        self,
        skip: int = 0,
        limit: int = 100,
        subject: str | None = None,
        grade: str | None = None,
        doc_type: str | None = None,
    ) -> tuple[list[Plan], int]:
        """Return filtered lesson plans and their total count."""
        filters = []
        if subject:
            filters.append(Plan.subject.ilike(f"%{subject.strip()}%"))
        if grade:
            filters.append(Plan.grade.ilike(f"%{grade.strip()}%"))
        if doc_type:
            filters.append(Plan.doc_type == doc_type)

        items_stmt = select(Plan).where(Plan.user_id == self.user_id).order_by(Plan.created_at.desc()).offset(skip).limit(limit)
        total_stmt = select(func.count()).select_from(Plan).where(Plan.user_id == self.user_id)

        for condition in filters:
            items_stmt = items_stmt.where(condition)
            total_stmt = total_stmt.where(condition)

        items_result = self.db.execute(items_stmt)
        total_result = self.db.execute(total_stmt)
        return list(items_result.scalars().all()), int(total_result.scalar_one())

    def search(
        self,
        query: str,
        skip: int = 0,
        limit: int = 100,
        doc_type: str | None = None,
    ) -> tuple[list[Plan], int]:
        """Search lesson plans by title keyword."""
        keyword = f"%{query}%"
        items_stmt = select(Plan).where(Plan.user_id == self.user_id, Plan.title.like(keyword))
        total_stmt = select(func.count()).select_from(Plan).where(Plan.user_id == self.user_id, Plan.title.like(keyword))
        if doc_type:
            items_stmt = items_stmt.where(Plan.doc_type == doc_type)
            total_stmt = total_stmt.where(Plan.doc_type == doc_type)
        items_stmt = items_stmt.order_by(Plan.created_at.desc()).offset(skip).limit(limit)

        items_result = self.db.execute(items_stmt)
        total_result = self.db.execute(total_stmt)
        return list(items_result.scalars().all()), int(total_result.scalar_one())

    def update(self, plan_id: str, data: PlanUpdate) -> Plan | None:
        """Update an existing lesson plan."""
        plan = self.get(plan_id)
        if plan is None:
            return None

        updates = data.model_dump(exclude_unset=True)
        if "metadata" in updates:
            updates["metadata_json"] = updates.pop("metadata")
        doc_type = updates.get("doc_type", plan.doc_type)
        if doc_type == "presentation" and ("title" in updates or "content" in updates):
            raw_content = updates.get("content")
            if not isinstance(raw_content, dict):
                raw_content = (
                    deepcopy(plan.content)
                    if isinstance(plan.content, dict)
                    else {"classroom_script": "", "slides": []}
                )
            raw_content["title"] = updates.get("title", plan.title)
            raw_content.setdefault("classroom_script", "")
            raw_content.setdefault("slides", [])
            updates["content"] = raw_content

        for field, value in updates.items():
            setattr(plan, field, value)
        plan.updated_at = datetime.now(timezone.utc)

        try:
            self.db.commit()
            self.db.refresh(plan)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to update plan.") from exc
        return plan

    def delete(self, plan_id: str) -> bool:
        """Delete a lesson plan by id."""
        plan = self.get(plan_id)
        if plan is None:
            return False

        try:
            self.db.delete(plan)
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to delete plan.") from exc
        return True
