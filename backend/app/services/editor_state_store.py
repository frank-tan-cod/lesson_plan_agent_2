"""Conversation-scoped state helpers for editor runtimes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from ..models import Conversation
from ..schemas import ConversationUpdate, Task
from .conversation_service import ConversationService
from .operation_service import OperationService
from .plan_service import PlanService


class EditorStateStore:
    """Read and persist editor interruption state in conversation metadata."""

    def __init__(
        self,
        *,
        conv_service: ConversationService,
        run_db: Callable[[Callable[[PlanService, ConversationService, OperationService], Any]], Awaitable[Any]],
        json_ready: Callable[[Any], Any],
        truncate_text: Callable[[str, int], str],
        recent_turn_key: str,
        recent_turn_limit: int,
        recent_turn_chars: int,
    ) -> None:
        self.conv_service = conv_service
        self.run_db = run_db
        self.json_ready = json_ready
        self.truncate_text = truncate_text
        self.recent_turn_key = recent_turn_key
        self.recent_turn_limit = recent_turn_limit
        self.recent_turn_chars = recent_turn_chars

    def get_recent_turns(self, conversation: Conversation) -> list[dict[str, Any]]:
        """Load recent remembered turns from conversation metadata."""
        metadata = conversation.metadata_json or {}
        turns = metadata.get(self.recent_turn_key)
        if not isinstance(turns, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            normalized.append(
                {
                    "role": str(item.get("role") or "assistant"),
                    "content": content,
                    "kind": str(item.get("kind") or "message"),
                }
            )
        return normalized[-self.recent_turn_limit :]

    async def append_recent_turn(self, conversation_id: str, role: str, content: str, *, kind: str = "message") -> None:
        """Persist a short turn memory for future prompt assembly."""
        normalized = self.truncate_text(" ".join(content.split()), self.recent_turn_chars)
        if not normalized:
            return

        conversation = self.conv_service.get(conversation_id)
        if conversation is None:
            return

        await self.update_metadata(
            conversation_id,
            {
                self.recent_turn_key: [
                    *self.get_recent_turns(conversation),
                    {"role": role, "content": normalized, "kind": kind},
                ][-self.recent_turn_limit :]
            },
        )

    def get_pending_tasks(self, conversation: Conversation) -> list[Task]:
        """Load queued tasks from conversation metadata."""
        metadata = conversation.metadata_json or {}
        return self.load_tasks_from_payload(metadata.get("pending_tasks"))

    async def save_pending_tasks(self, conversation_id: str, tasks: list[Task]) -> None:
        """Persist the remaining task queue into conversation metadata."""
        await self.update_metadata(
            conversation_id,
            {"pending_tasks": [task.model_dump() for task in tasks]},
        )

    async def clear_pending_tasks(self, conversation_id: str) -> None:
        """Remove the queued tasks from conversation metadata."""
        await self.update_metadata(conversation_id, {"pending_tasks": None})

    def get_pending_follow_up(self, conversation: Conversation) -> dict[str, Any] | None:
        """Read the persisted follow-up payload from conversation metadata."""
        return self._get_pending_payload(conversation, "pending_follow_up")

    async def save_pending_follow_up(self, conversation_id: str, follow_up: dict[str, Any]) -> None:
        """Persist the current interruption point for a later resume."""
        await self.update_metadata(conversation_id, {"pending_follow_up": follow_up})

    async def clear_pending_follow_up(self, conversation_id: str) -> None:
        """Remove the stored follow-up interruption marker."""
        await self.update_metadata(conversation_id, {"pending_follow_up": None})

    def get_pending_confirmation(self, conversation: Conversation) -> dict[str, Any] | None:
        """Read the persisted confirmation payload from conversation metadata."""
        return self._get_pending_payload(conversation, "pending_confirmation")

    async def save_pending_confirmation(self, conversation_id: str, confirmation: dict[str, Any]) -> None:
        """Persist the current confirmation request for a later confirm/cancel decision."""
        await self.update_metadata(conversation_id, {"pending_confirmation": confirmation})

    async def clear_pending_confirmation(self, conversation_id: str) -> None:
        """Remove the stored confirmation interruption marker."""
        await self.update_metadata(conversation_id, {"pending_confirmation": None})

    def load_tasks_from_payload(self, raw_tasks: Any) -> list[Task]:
        """Parse a loose task list payload into validated tasks."""
        if not isinstance(raw_tasks, list):
            return []

        tasks: list[Task] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(Task.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        return tasks

    async def update_metadata(self, conversation_id: str, changes: dict[str, Any | None]) -> None:
        """Merge conversation metadata updates using a fresh database session."""

        def operation(_: PlanService, conv_service: ConversationService, __: OperationService) -> Any:
            conversation = conv_service.get(conversation_id)
            if conversation is None:
                raise ValueError("Conversation not found.")

            metadata = deepcopy(conversation.metadata_json or {})
            for key, value in changes.items():
                if value is None:
                    metadata.pop(key, None)
                else:
                    metadata[key] = self.json_ready(value)
            return conv_service.update(conversation_id, ConversationUpdate(metadata=metadata))

        await self.run_db(operation)

    def _get_pending_payload(self, conversation: Conversation, key: str) -> dict[str, Any] | None:
        """Read one pending payload object from conversation metadata."""
        metadata = conversation.metadata_json or {}
        pending = metadata.get(key)
        return deepcopy(pending) if isinstance(pending, dict) else None
