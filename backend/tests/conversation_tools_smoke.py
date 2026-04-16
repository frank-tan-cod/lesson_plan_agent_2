"""Smoke test for conversation summary tools without vector dependencies."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.models import Conversation, Plan
from backend.app.tools.conversation_tools import register_conversation_tools
from backend.tools import ToolExecutor, ToolsRegistry


async def main() -> None:
    """Create a summarized conversation and verify both conversation tools."""
    init_db()

    plan_id: str | None = None
    conversation_id: str | None = None

    registry = ToolsRegistry()
    register_conversation_tools(registry)
    executor = ToolExecutor(registry)

    with session_maker() as session:
        try:
            plan = Plan(
                title="牛顿第一定律教案",
                subject="物理",
                grade="八年级",
                doc_type="lesson",
                content={"sections": []},
                metadata_json={},
            )
            session.add(plan)
            session.commit()
            session.refresh(plan)
            plan_id = plan.id

            conversation = Conversation(
                plan_id=plan.id,
                status="archived",
                summary="讨论了惯性实验导入、板书结构和练习节奏，决定强化生活化案例。",
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            conversation_id = conversation.id

            search_result = await executor.execute(
                "search_conversation_summaries",
                {"query": "惯性实验导入", "top_k": 3},
            )
            detail_result = await executor.execute(
                "get_conversation_summary",
                {"conversation_id": conversation.id},
            )

            assert search_result["ok"] is True
            assert search_result["results"]
            assert search_result["results"][0]["conversation_id"] == conversation.id
            assert "牛顿第一定律教案" in search_result["message"]

            assert detail_result["ok"] is True
            assert detail_result["conversation_id"] == conversation.id
            assert "强化生活化案例" in detail_result["summary"]
            assert "牛顿第一定律教案" in detail_result["message"]

            print("Conversation tools smoke test passed.")
            print(search_result["message"])
            print(detail_result["message"])
        finally:
            if conversation_id:
                stored_conversation = session.get(Conversation, conversation_id)
                if stored_conversation is not None:
                    session.delete(stored_conversation)
                    session.commit()
            if plan_id:
                stored_plan = session.get(Plan, plan_id)
                if stored_plan is not None:
                    session.delete(stored_plan)
                    session.commit()


if __name__ == "__main__":
    asyncio.run(main())
