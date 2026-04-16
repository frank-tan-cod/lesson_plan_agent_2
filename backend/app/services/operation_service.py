"""Service logic for operation log management."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import Conversation, Operation
from ..schemas import OperationCreate
from ..user_context import DEFAULT_USER_ID, resolve_user_id


class OperationService:
    """Encapsulates operation log persistence logic."""

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)

    def create(self, data: OperationCreate) -> Operation:
        """Record a tool execution for an existing conversation."""
        conversation = self.db.execute(
            select(Conversation).where(Conversation.id == data.conversation_id, Conversation.user_id == self.user_id)
        ).scalar_one_or_none()
        if conversation is None:
            raise ValueError("Conversation not found.")

        operation = Operation(
            user_id=self.user_id,
            conversation_id=data.conversation_id,
            tool_name=data.tool_name,
            arguments=self._json_ready(data.arguments),
            result=self._json_ready(data.result),
        )
        self.db.add(operation)
        try:
            self.db.commit()
            self.db.refresh(operation)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to create operation.") from exc
        return operation

    def _json_ready(self, value: Any) -> Any:
        """Convert arbitrary nested values into JSON-safe payloads for persistence."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): self._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_ready(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._json_ready(value.model_dump())
        if hasattr(value, "__dict__"):
            return self._json_ready(value.__dict__)
        return str(value)

    def list_by_conversation(self, conv_id: str, limit: int | None = None) -> list[Operation]:
        """List operation logs in ascending execution order."""
        stmt = (
            select(Operation)
            .where(Operation.conversation_id == conv_id, Operation.user_id == self.user_id)
            .order_by(Operation.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        result = self.db.execute(stmt)
        return list(result.scalars().all())
