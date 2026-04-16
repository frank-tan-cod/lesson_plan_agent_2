"""Smoke test for follow-up interruption and resume in the editor."""

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
    """Fake intent-recognition client with one follow-up round and one resume round."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        self.requests.append(messages)
        self.calls += 1

        if self.calls == 1:
            content = json.dumps(
                {
                    "tasks": [
                        {
                            "type": "follow_up",
                            "parameters": {
                                "question": "你希望小组讨论持续几分钟？",
                                "options": ["3分钟", "5分钟", "10分钟"],
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "tasks": [
                        {
                            "type": "reply",
                            "response": "收到，我会按 5 分钟的小组讨论来继续设计。",
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


async def main() -> None:
    """Run a smoke test for follow-up pause and resume."""
    init_db()

    fake_client = FakeClient()

    with session_maker() as session:
        plan_service = get_plan_service(session)
        conv_service = get_conversation_service(session)
        op_service = get_operation_service(session)

        plan = plan_service.create(
            PlanCreate(
                title="追问测试教案",
                subject="语文",
                grade="三年级",
                content={"sections": [{"type": "新授", "content": "先说明活动流程", "duration": 10}]},
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
        async for chunk in editor.process_message("请帮我设计一个小组讨论活动"):
            first_events.append(chunk)

        conversation_id = editor.conversation_id or ""
        operations = op_service.list_by_conversation(conversation_id)

    assert conversation_id
    assert any("event: follow_up" in item for item in first_events)
    assert not any("event: done" in item for item in first_events)
    assert not operations

    with session_maker() as session:
        conversation = get_conversation_service(session).get(conversation_id)
        assert conversation is not None
        pending = (conversation.metadata_json or {}).get("pending_follow_up")
        assert isinstance(pending, dict)
        assert pending["question"] == "你希望小组讨论持续几分钟？"
        assert pending["options"] == ["3分钟", "5分钟", "10分钟"]
        assert pending["previous_user_message"] == "请帮我设计一个小组讨论活动"
        assert "original_messages" not in pending

    with session_maker() as session:
        editor = DocumentEditor(
            plan_id=plan.id,
            conversation_id=conversation_id,
            plan_service=get_plan_service(session),
            conv_service=get_conversation_service(session),
            op_service=get_operation_service(session),
            tools_registry=registry,
            tool_executor=ToolExecutor(registry),
            db=session,
            db_factory=session_maker,
            llm_client=fake_client,
        )

        second_events = []
        async for chunk in editor.process_message("5分钟"):
            second_events.append(chunk)

    assert any("event: done" in item for item in second_events)
    assert any("5 分钟" in item or "5分钟" in item for item in second_events)

    resumed_prompt = fake_client.chat.completions.requests[-1][1]["content"]
    assert "当前用户正在回答上一轮追问" in resumed_prompt
    assert "上一轮追问：你希望小组讨论持续几分钟？" in resumed_prompt
    assert "上一轮原始需求：请帮我设计一个小组讨论活动" in resumed_prompt
    assert "用户消息：5分钟" in resumed_prompt

    with session_maker() as session:
        conversation = get_conversation_service(session).get(conversation_id)
        assert conversation is not None
        assert "pending_follow_up" not in (conversation.metadata_json or {})
        get_plan_service(session).delete(plan.id)

    print("Follow-up smoke test passed.")
    print(f"Conversation: {conversation_id}")
    print(f"Events: {len(first_events)} -> {len(second_events)}")


if __name__ == "__main__":
    asyncio.run(main())
