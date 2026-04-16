from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.editor_runtime import ConversationExecutionRegistry
from backend.tools.cancellation import CancellationToken


class ConversationExecutionRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_superseded_run_cancels_previous_and_waits_for_release(self) -> None:
        registry = ConversationExecutionRegistry()
        first_token = CancellationToken()
        second_token = CancellationToken()

        await registry.acquire("conv-1", first_token)
        pending_second = asyncio.create_task(registry.acquire("conv-1", second_token))
        await asyncio.sleep(0)

        self.assertTrue(first_token.is_cancelled())
        self.assertFalse(pending_second.done())

        await registry.release("conv-1", first_token)
        await asyncio.wait_for(pending_second, timeout=1)

        self.assertFalse(second_token.is_cancelled())

        await registry.release("conv-1", second_token)
