"""Service logic for savepoint management."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import Conversation, Plan, Savepoint
from ..schemas import SavepointCreate
from ..user_context import DEFAULT_USER_ID, resolve_user_id


class SavepointService:
    """Encapsulates savepoint persistence and restore logic."""

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)

    def create(self, data: SavepointCreate) -> Savepoint:
        """Create a savepoint snapshot for an existing lesson plan."""
        plan = self.db.execute(select(Plan).where(Plan.id == data.plan_id, Plan.user_id == self.user_id)).scalar_one_or_none()
        if plan is None:
            raise ValueError("Plan not found.")
        if data.conversation_id is not None:
            conversation = self.db.execute(
                select(Conversation).where(
                    Conversation.id == data.conversation_id,
                    Conversation.user_id == self.user_id,
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ValueError("Conversation not found.")

        savepoint = Savepoint(
            user_id=self.user_id,
            plan_id=data.plan_id,
            conversation_id=data.conversation_id,
            label=data.label,
            snapshot=data.snapshot,
        )
        self.db.add(savepoint)
        try:
            self.db.commit()
            self.db.refresh(savepoint)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to create savepoint.") from exc
        return savepoint

    def list_by_plan(self, plan_id: str) -> list[Savepoint]:
        """List savepoints for a lesson plan."""
        result = self.db.execute(
            select(Savepoint)
            .where(Savepoint.plan_id == plan_id, Savepoint.user_id == self.user_id)
            .order_by(Savepoint.created_at.desc())
        )
        return list(result.scalars().all())

    def restore(self, savepoint_id: str) -> Savepoint | None:
        """Restore a lesson plan's content from a savepoint snapshot."""
        savepoint = self.db.execute(
            select(Savepoint).where(Savepoint.id == savepoint_id, Savepoint.user_id == self.user_id)
        ).scalar_one_or_none()
        if savepoint is None:
            return None

        plan = self.db.execute(
            select(Plan).where(Plan.id == savepoint.plan_id, Plan.user_id == self.user_id)
        ).scalar_one_or_none()
        if plan is None:
            raise ValueError("Plan not found.")

        try:
            self.db.execute(
                update(Plan)
                .where(Plan.id == savepoint.plan_id, Plan.user_id == self.user_id)
                .values(content=savepoint.snapshot, updated_at=datetime.now(timezone.utc))
            )
            self.db.commit()
            self.db.refresh(savepoint)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to restore savepoint.") from exc
        return savepoint

    def delete(self, savepoint_id: str) -> bool:
        """Delete a savepoint by id."""
        savepoint = self.db.execute(
            select(Savepoint).where(Savepoint.id == savepoint_id, Savepoint.user_id == self.user_id)
        ).scalar_one_or_none()
        if savepoint is None:
            return False

        try:
            self.db.delete(savepoint)
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to delete savepoint.") from exc
        return True
