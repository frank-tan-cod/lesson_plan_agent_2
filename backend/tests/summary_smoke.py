"""Smoke test for conversation summary generation and semantic search."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.models import Conversation, Operation, Plan
from backend.app.services.conversation_service import ConversationService
from backend.app.services.summary_service import ConversationSummaryService


class FakeEmbeddingService:
    """Deterministic embedding stub for smoke testing."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = text.strip()
            char_total = sum(ord(char) for char in normalized)
            vectors.append(
                [
                    round(len(normalized) / 1000, 6),
                    round((char_total % 997) / 997, 6),
                    round((len(set(normalized)) or 1) / 200, 6),
                ]
            )
        return vectors


class FakeVectorStore:
    """Minimal in-memory vector store used by the smoke test."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, object]] = {}

    def upsert(
        self,
        *,
        conversation_id: str,
        summary: str,
        embedding: list[float],
        metadata: dict[str, object],
    ) -> None:
        self.entries[conversation_id] = {
            "summary": summary,
            "embedding": embedding,
            "metadata": metadata,
        }

    def query(self, *, query_embedding: list[float], top_k: int) -> list[dict[str, object]]:
        ranked: list[dict[str, object]] = []
        for conversation_id, payload in self.entries.items():
            embedding = payload["embedding"]
            distance = math.sqrt(sum((left - right) ** 2 for left, right in zip(query_embedding, embedding)))
            ranked.append(
                {
                    "conversation_id": conversation_id,
                    "summary": payload["summary"],
                    "metadata": payload["metadata"],
                    "distance": distance,
                }
            )
        ranked.sort(key=lambda item: float(item["distance"]))
        return ranked[:top_k]


class FakeLLMClient:
    """OpenAI-compatible stub that always returns a fixed summary."""

    def __init__(self, summary_text: str) -> None:
        self.summary_text = summary_text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_: object):
        message = SimpleNamespace(content=self.summary_text)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def main() -> None:
    """Run a lightweight end-to-end smoke verification."""
    init_db()

    fake_vector_store = FakeVectorStore()
    fake_embedding_service = FakeEmbeddingService()
    fake_llm_client = FakeLLMClient(
        "围绕《惯性实验教案》讨论了导入实验、板书结构和课堂节奏，最终决定强化生活化情境导入，并压缩讲授时长以留出学生探究时间。"
    )

    plan_id: str | None = None
    conversation_id: str | None = None

    with session_maker() as session:
        try:
            plan = Plan(
                title="惯性实验教案",
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

            conversation = Conversation(plan_id=plan.id)
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            conversation_id = conversation.id

            session.add_all(
                [
                    Operation(
                        conversation_id=conversation.id,
                        tool_name="rewrite_section",
                        arguments={"section_type": "导入", "instruction": "改成生活化惯性实验导入"},
                        result={"status": "ok"},
                    ),
                    Operation(
                        conversation_id=conversation.id,
                        tool_name="adjust_duration",
                        arguments={"section_type": "新授", "duration": 18},
                        result={"status": "ok"},
                    ),
                ]
            )
            session.commit()

            ConversationService(session).end(conversation.id)

            service = ConversationSummaryService(
                session,
                embedding_service=fake_embedding_service,
                llm_client=fake_llm_client,
                vector_store=fake_vector_store,
            )
            result = service.generate_summary(conversation.id)
            refreshed = session.get(Conversation, conversation.id)
            hits = service.search("惯性实验讨论", top_k=5)

            assert result.summary, "摘要未生成。"
            assert result.indexed is True, "摘要未建立索引。"
            assert refreshed is not None and refreshed.summary == result.summary, "摘要未写回会话记录。"
            assert hits, "搜索未返回结果。"
            assert hits[0]["conversation_id"] == conversation.id, "搜索结果未命中目标会话。"

            print("summary smoke passed")
            print(f"conversation_id={conversation.id}")
            print(f"summary={result.summary}")
        finally:
            if plan_id:
                stored_plan = session.get(Plan, plan_id)
                if stored_plan is not None:
                    session.delete(stored_plan)
                    session.commit()
            elif conversation_id:
                stored_conversation = session.get(Conversation, conversation_id)
                if stored_conversation is not None:
                    session.delete(stored_conversation)
                    session.commit()


if __name__ == "__main__":
    main()
