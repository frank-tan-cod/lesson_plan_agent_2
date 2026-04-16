"""Runtime coordination helpers for streaming editor requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ...tools.cancellation import CancellationToken


@dataclass(slots=True)
class _ConversationExecutionState:
    """Track one conversation's active streaming request."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_token: CancellationToken | None = None
    leases: int = 0


class ConversationExecutionRegistry:
    """Serialize writes per conversation and cancel superseded runs."""

    def __init__(self) -> None:
        self._states: dict[str, _ConversationExecutionState] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, conversation_id: str, token: CancellationToken) -> None:
        """Register one active run and cancel any previous run for the conversation."""
        async with self._guard:
            state = self._states.setdefault(conversation_id, _ConversationExecutionState())
            state.leases += 1
            previous_token = state.active_token
            if previous_token is not None and previous_token is not token:
                previous_token.cancel()

        await state.lock.acquire()

        async with self._guard:
            state.active_token = token

    async def release(self, conversation_id: str, token: CancellationToken) -> None:
        """Release the active run slot for one conversation."""
        async with self._guard:
            state = self._states.get(conversation_id)
            if state is None:
                return
            if state.active_token is token:
                state.active_token = None
            if state.lock.locked():
                state.lock.release()
            state.leases = max(state.leases - 1, 0)
            if state.leases == 0 and not state.lock.locked() and state.active_token is None:
                self._states.pop(conversation_id, None)


conversation_execution_registry = ConversationExecutionRegistry()


__all__ = ["ConversationExecutionRegistry", "conversation_execution_registry"]
