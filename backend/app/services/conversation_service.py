"""Service logic for conversation management."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import Conversation, Plan
from ..schemas import ConversationUpdate
from ..temp_preferences import normalize_temp_preferences_payload
from ..user_context import DEFAULT_USER_ID, resolve_user_id


class ConversationService:
    """Encapsulates conversation persistence logic."""

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)

    def create(self, plan_id: str) -> Conversation:
        """Start a new conversation for an existing lesson plan."""
        plan = self.db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.user_id == self.user_id)
        ).scalar_one_or_none()
        if plan is None:
            raise ValueError("Plan not found.")

        conversation = Conversation(plan_id=plan_id, user_id=self.user_id)
        self.db.add(conversation)
        try:
            self.db.commit()
            self.db.refresh(conversation)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to create conversation.") from exc
        return conversation

    def get(self, conv_id: str) -> Conversation | None:
        """Fetch a single conversation by id."""
        return self.db.execute(
            select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == self.user_id)
        ).scalar_one_or_none()

    def list_by_plan(self, plan_id: str) -> list[Conversation]:
        """List all conversations for a lesson plan."""
        result = self.db.execute(
            select(Conversation)
            .where(Conversation.plan_id == plan_id, Conversation.user_id == self.user_id)
            .order_by(Conversation.started_at.desc())
        )
        return list(result.scalars().all())

    def end(self, conv_id: str) -> Conversation | None:
        """Archive an active conversation."""
        conversation = self.get(conv_id)
        if conversation is None:
            return None

        conversation.ended_at = datetime.now(timezone.utc)
        conversation.status = "archived"
        try:
            self.db.commit()
            self.db.refresh(conversation)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to end conversation.") from exc
        return conversation

    def update(self, conv_id: str, data: ConversationUpdate) -> Conversation | None:
        """Update conversation fields such as summary or metadata."""
        conversation = self.get(conv_id)
        if conversation is None:
            return None

        updates = data.model_dump(exclude_unset=True)
        if "metadata" in updates:
            updates["metadata_json"] = updates.pop("metadata")

        for field, value in updates.items():
            setattr(conversation, field, value)

        try:
            self.db.commit()
            self.db.refresh(conversation)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to update conversation.") from exc
        return conversation

    def search(self, query: str) -> list[Conversation]:
        """Search conversations by summary text."""
        keyword = f"%{query}%"
        result = self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == self.user_id)
            .where(Conversation.summary.is_not(None))
            .where(Conversation.summary.like(keyword))
            .order_by(Conversation.started_at.desc())
        )
        return list(result.scalars().all())

    def get_temp_preferences(self, conv_id: str) -> dict[str, Any] | None:
        """Return the temporary preference object stored in conversation metadata."""
        conversation = self.get(conv_id)
        if conversation is None:
            return None

        metadata = deepcopy(conversation.metadata_json or {})
        temp_preferences = metadata.get("temp_preferences")
        return normalize_temp_preferences_payload(deepcopy(temp_preferences) if isinstance(temp_preferences, dict) else {})

    def replace_temp_preferences(self, conv_id: str, data: dict[str, Any]) -> Conversation | None:
        """Replace the whole temporary preference object for a conversation."""
        conversation = self.get(conv_id)
        if conversation is None:
            return None

        metadata = deepcopy(conversation.metadata_json or {})
        metadata["temp_preferences"] = normalize_temp_preferences_payload(deepcopy(data))
        conversation.metadata_json = metadata
        return self._save(conversation, "Failed to replace temporary preferences.")

    def patch_temp_preferences(self, conv_id: str, data: dict[str, Any]) -> Conversation | None:
        """Merge temporary preference updates into the existing metadata payload."""
        conversation = self.get(conv_id)
        if conversation is None:
            return None

        metadata = deepcopy(conversation.metadata_json or {})
        existing = metadata.get("temp_preferences")
        merged = normalize_temp_preferences_payload(deepcopy(existing) if isinstance(existing, dict) else {})
        merged.update(deepcopy(data))
        metadata["temp_preferences"] = normalize_temp_preferences_payload(merged)
        conversation.metadata_json = metadata
        return self._save(conversation, "Failed to update temporary preferences.")

    def _save(self, conversation: Conversation, error_message: str) -> Conversation:
        """Persist a modified conversation and refresh the instance."""
        try:
            self.db.commit()
            self.db.refresh(conversation)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError(error_message) from exc
        return conversation
