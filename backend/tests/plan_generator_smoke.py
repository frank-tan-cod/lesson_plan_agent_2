"""Smoke test for lesson-plan draft generation during plan creation."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.routers.plans import create_plan
from backend.app.schemas import PlanCreate
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService


class InMemoryVectorStore:
    """Lightweight vector-store stub for knowledge uploads in smoke tests."""

    def add_entries(self, user_id: str, entries: list[IndexedEntry]) -> None:
        return None

    def query(self, user_id: str, query_embedding: list[float], *, top_k: int, file_type: str | None = None) -> list[dict]:
        return []

    def delete_file(self, user_id: str, file_id: str) -> None:
        return None


class FakeCompletions:
    """Fake sync chat completion API that returns lesson-plan JSON."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def create(self, **_: object) -> object:
        messages = _.get("messages") if isinstance(_, dict) else None
        if isinstance(messages, list) and messages:
            user_message = messages[-1].get("content")
            if isinstance(user_message, str):
                self.requests.append(user_message)
        payload = {
            "title": "浮力初稿",
            "sections": [
                {"type": "教学目标", "content": "明确浮力概念并能说出影响因素。", "duration": 0},
                {"type": "导入", "content": "用物体沉浮现象导入，激发学生猜想。", "duration": 5},
                {"type": "新授", "content": "通过实验讲解浮力方向、大小和阿基米德原理。", "duration": 25},
                {"type": "巩固练习", "content": "安排分层练习题并讨论实验现象。", "duration": 10},
                {"type": "小结作业", "content": "总结本课要点并布置生活观察作业。", "duration": 5},
            ],
            "metadata": {"subject": "物理", "grade": "八年级"},
        }
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Minimal sync OpenAI-compatible client surface."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


async def main() -> None:
    """Run a route-level smoke test without calling a real LLM."""
    init_db()
    fake_client = FakeClient()

    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("backend.app.services.plan_generator._get_llm_client", return_value=fake_client):
            with session_maker() as session:
                knowledge_service = KnowledgeService(
                    session,
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )
                reference_file = await knowledge_service.add_document(
                    "default",
                    "float-reference.md",
                    "# 浮力实验\n把鸡蛋放入盐水前后观察沉浮变化，并记录现象。".encode("utf-8"),
                    description="浮力实验记录",
                )

                generated_plan = await create_plan(
                    PlanCreate(
                        title="浮力",
                        subject="物理",
                        grade="八年级",
                        requirements="45分钟，包含导入、新授、巩固练习、小结作业",
                        course_context="希望结合鸡蛋沉浮实验和生活中的船只案例。",
                        additional_files=[reference_file.id],
                        metadata={"source": "smoke-test"},
                    ),
                    db=session,
                )
                plain_plan = await create_plan(
                    PlanCreate(
                        title="未生成教案",
                        subject="数学",
                        grade="五年级",
                    ),
                    db=session,
                )

    generated_payload = generated_plan.model_dump(mode="json")
    assert generated_payload["content"]["sections"]
    assert generated_payload["content"]["sections"][0]["type"] == "教学目标"
    assert generated_payload["metadata"]["subject"] == "物理"
    assert generated_payload["metadata"]["source"] == "smoke-test"
    assert generated_payload["content"]["metadata"]["source"] == "smoke-test"
    assert generated_payload["metadata"]["creation_additional_file_ids"] == [reference_file.id]
    assert "鸡蛋沉浮实验" in fake_client.chat.completions.requests[0]
    assert "生活中的船只案例" in fake_client.chat.completions.requests[0]

    plain_payload = plain_plan.model_dump(mode="json")
    assert plain_payload["content"] == {"sections": []}
    assert plain_payload["metadata"] == {}

    print("Plan generator smoke test passed.")
    print(f"Generated plan id: {generated_payload['id']}")
    print(f"Generated sections: {len(generated_payload['content']['sections'])}")


if __name__ == "__main__":
    asyncio.run(main())
