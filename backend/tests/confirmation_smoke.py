"""Smoke test for confirmation interruption, confirm, and cancel flows."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.dependencies import get_conversation_service, get_operation_service, get_plan_service
from backend.app.schemas import PlanCreate
from backend.app.services import DocumentEditor
from backend.app.tools import register_lesson_tools
from backend.tools import ToolExecutor, ToolsRegistry


class FakeCompletions:
    """Fake intent-recognition client that plans one destructive modify task."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        self.requests.append(messages)
        content = json.dumps(
            {
                "tasks": [
                    {
                        "type": "modify",
                        "tool_name": "delete_section",
                        "target": "巩固练习",
                        "action": "delete",
                        "proposed_content": "删除整个巩固练习章节。",
                        "parameters": {"section_type": "巩固练习"},
                    }
                ]
            },
            ensure_ascii=False,
        )
        message = SimpleNamespace(content=content, tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Minimal OpenAI-compatible client surface for the editor."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


async def run_confirm_flow() -> None:
    """Verify pending confirmation is stored and confirmed tool executes."""
    fake_client = FakeClient()

    with session_maker() as session:
        plan_service = get_plan_service(session)
        conv_service = get_conversation_service(session)
        op_service = get_operation_service(session)

        plan = plan_service.create(
            PlanCreate(
                title="确认测试教案",
                subject="语文",
                grade="四年级",
                content={
                    "sections": [
                        {"type": "导入", "content": "导入活动", "duration": 5},
                        {"type": "巩固练习", "content": "练习部分", "duration": 10},
                        {"type": "总结", "content": "总结内容", "duration": 5},
                    ]
                },
            )
        )

        registry = register_lesson_tools(ToolsRegistry())
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
            llm_client=fake_client,
        )

        first_events = []
        async for chunk in editor.process_message("请删除巩固练习章节"):
            first_events.append(chunk)

        conversation_id = editor.conversation_id or ""
        assert conversation_id
        assert any("event: confirmation_required" in item for item in first_events)

        pending = (conv_service.get(conversation_id).metadata_json or {}).get("pending_confirmation")  # type: ignore[union-attr]
        assert isinstance(pending, dict)
        assert pending["operation_description"] == "准备delete“巩固练习”"
        assert pending["tool_to_confirm"] == "delete_section"

        second_events = []
        async for chunk in editor.process_message("/confirm"):
            second_events.append(chunk)

        refreshed_plan = plan_service.get(plan.id)
        operations = op_service.list_by_conversation(conversation_id)

    assert refreshed_plan is not None
    section_names = [item.get("type") for item in refreshed_plan.content["sections"]]
    assert "巩固练习" not in section_names
    assert any("event: done" in item for item in second_events)
    assert any(item.tool_name == "delete_section" for item in operations)

    with session_maker() as session:
        conversation = get_conversation_service(session).get(conversation_id)
        assert conversation is not None
        assert "pending_confirmation" not in (conversation.metadata_json or {})
        get_plan_service(session).delete(plan.id)


async def run_cancel_flow() -> None:
    """Verify cancel clears pending state without executing the dangerous tool."""
    fake_client = FakeClient()

    with session_maker() as session:
        plan_service = get_plan_service(session)
        conv_service = get_conversation_service(session)
        op_service = get_operation_service(session)

        plan = plan_service.create(
            PlanCreate(
                title="取消确认测试教案",
                subject="数学",
                grade="五年级",
                content={
                    "sections": [
                        {"type": "导入", "content": "导入活动", "duration": 5},
                        {"type": "巩固练习", "content": "练习部分", "duration": 10},
                        {"type": "总结", "content": "总结内容", "duration": 5},
                    ]
                },
            )
        )

        registry = register_lesson_tools(ToolsRegistry())
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
            llm_client=fake_client,
        )

        async for _ in editor.process_message("请删除巩固练习章节"):
            pass

        conversation_id = editor.conversation_id or ""
        assert conversation_id

        cancel_events = []
        async for chunk in editor.process_message("/cancel"):
            cancel_events.append(chunk)

        refreshed_plan = plan_service.get(plan.id)
        operations = op_service.list_by_conversation(conversation_id)

    assert refreshed_plan is not None
    section_names = [item.get("type") for item in refreshed_plan.content["sections"]]
    assert "巩固练习" in section_names
    assert any("event: done" in item for item in cancel_events)
    assert any("已取消" in item for item in cancel_events)
    assert not any(item.tool_name == "delete_section" for item in operations)

    with session_maker() as session:
        conversation = get_conversation_service(session).get(conversation_id)
        assert conversation is not None
        assert "pending_confirmation" not in (conversation.metadata_json or {})
        get_plan_service(session).delete(plan.id)


async def main() -> None:
    """Run confirmation smoke tests."""
    init_db()
    await run_confirm_flow()
    await run_cancel_flow()
    print("Confirmation smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
