"""Smoke test for the document editor workflow."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.dependencies import get_conversation_service, get_operation_service, get_plan_service
from backend.app.schemas import PlanCreate
from backend.app.services import DocumentEditor
from backend.tools import ToolExecutor, ToolsRegistry, tool


class MockUpdateArgs(BaseModel):
    """Arguments accepted by the mock update tool."""

    note: str = Field(..., description="User request.")


@tool(
    name="mock_update_plan",
    description="Update the lesson plan content inside the smoke test.",
    args_schema=MockUpdateArgs,
)
def mock_update_plan(note: str) -> dict[str, Any]:
    """Return updated plan content for the smoke test."""
    return {
        "updated_content": {
            "sections": [
                {
                    "title": "课堂导入",
                    "content": f"根据请求调整：{note}",
                }
            ]
        }
    }


class FakeCompletions:
    """Fake planner client that returns one task-plan JSON response."""

    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **_: Any) -> Any:
        self.calls += 1
        message = SimpleNamespace(
            content=json.dumps(
                {
                    "goal_status": "need_more_steps",
                    "tasks": [
                        {
                            "type": "modify",
                            "tool_name": "mock_update_plan",
                            "target": "课堂导入",
                            "action": "rewrite",
                            "proposed_content": "补充一个课堂导入活动。",
                            "parameters": {"note": "补充导入活动"},
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Minimal OpenAI-compatible client surface for the editor."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


async def main() -> None:
    """Run a simple end-to-end smoke test without calling a real LLM."""
    init_db()

    with session_maker() as session:
        plan_service = get_plan_service(session)
        conv_service = get_conversation_service(session)
        op_service = get_operation_service(session)

        plan = plan_service.create(
            PlanCreate(
                title="编辑器测试教案",
                subject="语文",
                grade="三年级",
                content={"sections": []},
            )
        )

        registry = ToolsRegistry()
        registry.register(mock_update_plan)
        editor = DocumentEditor(
            plan_id=plan.id,
            conversation_id=None,
            plan_service=plan_service,
            conv_service=conv_service,
            op_service=op_service,
            tools_registry=registry,
            tool_executor=ToolExecutor(registry),
            db=session,
            db_factory=session_maker,
            llm_client=FakeClient(),
        )

        first_events = []
        async for chunk in editor.process_message("请补充一个课堂导入活动"):
            first_events.append(chunk)

        conversation_id = editor.conversation_id or ""
        assert conversation_id
        assert any("event: confirmation_required" in item for item in first_events)

        second_events = []
        async for chunk in editor.process_message("/confirm"):
            second_events.append(chunk)

        refreshed_plan = plan_service.get(plan.id)
        operations = op_service.list_by_conversation(conversation_id)

    assert refreshed_plan is not None
    assert refreshed_plan.content["sections"][0]["content"] == "根据请求调整：补充导入活动"
    assert operations
    assert any("event: done" in item for item in second_events)

    print("Smoke test passed.")
    print(f"Conversation: {conversation_id}")
    print(f"Updated content: {json.dumps(refreshed_plan.content, ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
