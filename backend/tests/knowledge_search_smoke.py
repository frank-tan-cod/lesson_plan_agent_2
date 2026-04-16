"""Smoke test for auto-ingested lesson/PPT knowledge and hybrid search organization."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeEmbeddingService:
    """Deterministic embedding provider for hybrid-search smoke testing."""

    KEYWORDS = ("浮力", "实验", "展示", "课堂", "课件", "教案")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [float(text.count(keyword)) for keyword in self.KEYWORDS]
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            vectors.append([item / norm for item in vector])
        return vectors


class InMemoryVectorStore:
    """Tiny in-memory vector store used to avoid external dependencies."""

    def __init__(self) -> None:
        self._items: dict[str, list[Any]] = {}

    def add_entries(self, user_id: str, entries: list[Any]) -> None:
        bucket = self._items.setdefault(user_id, [])
        bucket.extend(entries)

    def query(
        self,
        user_id: str,
        query_embedding: list[float],
        *,
        top_k: int,
        file_type: str | None = None,
    ) -> list[dict[str, Any]]:
        bucket = self._items.get(user_id, [])
        ranked: list[tuple[float, Any]] = []
        for item in bucket:
            if file_type and item.metadata.get("file_type") != file_type:
                continue
            distance = sum(abs(left - right) for left, right in zip(query_embedding, item.embedding))
            ranked.append((distance, item))
        ranked.sort(key=lambda pair: pair[0])
        return [
            {"text": entry.text, "metadata": entry.metadata, "distance": distance}
            for distance, entry in ranked[:top_k]
        ]

    def delete_file(self, user_id: str, file_id: str) -> None:
        bucket = self._items.get(user_id, [])
        self._items[user_id] = [item for item in bucket if item.metadata.get("file_id") != file_id]


class _FakeCompletions:
    def create(self, **kwargs: Any) -> SimpleNamespace:
        prompt = str(kwargs.get("messages", [{}])[-1].get("content") or "")
        start_token = "候选结果：\n"
        end_token = "\n\n要求："
        start = prompt.index(start_token) + len(start_token)
        end = prompt.index(end_token, start)
        candidates = json.loads(prompt[start:end].strip())
        ranked = sorted(
            candidates,
            key=lambda item: (
                1 if item.get("doc_type") == "presentation" else 0,
                float(item.get("relevance_score") or 0.0),
            ),
            reverse=True,
        )
        payload = {
            "results": [
                {
                    "file_id": item["file_id"],
                    "relevance_score": min(0.99, 0.9 - index * 0.08),
                    "summary": (
                        "这份 PPT 初稿更适合参考页面组织和课堂展示语言。"
                        if item.get("doc_type") == "presentation"
                        else "这份教案更适合参考教学环节安排。"
                    ),
                    "match_reason": (
                        "查询明确提到展示结构和 PPT，和这份课件草稿更贴近。"
                        if item.get("doc_type") == "presentation"
                        else "内容相关，但更偏向教学设计层。"
                    ),
                }
                for index, item in enumerate(ranked)
            ]
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ]
        )


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class FakeLLMClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


async def main() -> None:
    """Exercise auto-ingestion plus hybrid knowledge retrieval."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "knowledge_search.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"

        from backend.app.database import init_db, session_maker
        from backend.app.schemas import PlanCreate, PresentationCreate
        from backend.app.services.knowledge_service import KnowledgeService
        from backend.app.services.plan_ingestion import auto_ingest_plan, auto_ingest_presentation
        from backend.app.services.plan_service import PlanService
        from backend.app.services.presentation_service import PresentationService

        init_db()

        with session_maker() as session:
            knowledge_service = KnowledgeService(
                session,
                user_id="user-1",
                embedding_service=FakeEmbeddingService(),
                llm_client=FakeLLMClient(),
                vector_store=InMemoryVectorStore(),
                base_dir=temp_dir,
            )
            plan_service = PlanService(session, user_id="user-1")
            presentation_service = PresentationService(session, user_id="user-1")

            lesson = plan_service.create(
                PlanCreate(
                    title="浮力实验教案",
                    subject="物理",
                    grade="八年级",
                    content={
                        "sections": [
                            {"title": "导入", "content": "通过浮力实验引出本课问题。"},
                            {"title": "新授", "content": "分析物体在液体中的受力与展示要点。"},
                        ]
                    },
                )
            )
            presentation = presentation_service.create(
                PresentationCreate(
                    title="浮力实验展示课件",
                    content={
                        "title": "浮力实验展示课件",
                        "classroom_script": "围绕浮力实验组织课堂展示，先看现象，再归纳受力规律。",
                        "slides": [
                            {
                                "template": "title_body_image",
                                "title": "浮力实验现象",
                                "body": "观察木块和铁块在水中的状态\n归纳展示结论",
                                "image_description": "水槽实验示意图",
                                "notes": "先展示现象，再追问原因。",
                                "source_section": "导入",
                            },
                            {
                                "template": "title_body",
                                "title": "受力分析",
                                "body": "比较重力和浮力\n形成课堂板书提纲",
                                "notes": "引导学生口头表达。",
                                "source_section": "新授",
                            },
                        ],
                    },
                    metadata={"generated_from": "lesson_plan", "source_plan_id": lesson.id},
                )
            )

            lesson_file = await auto_ingest_plan(lesson.id, session, knowledge_service, user_id="user-1", trigger="create")
            presentation_file = await auto_ingest_presentation(
                presentation.id,
                session,
                knowledge_service,
                user_id="user-1",
                trigger="generate_presentation",
            )
            files, total = knowledge_service.list_files(user_id="user-1")
            results = await knowledge_service.search(
                "user-1",
                "我想找一份能参考浮力实验展示结构的 PPT 资料",
                top_k=3,
            )

        assert lesson_file is not None
        assert presentation_file is not None
        assert total == 2
        assert any(item.filename.startswith("PPT初稿_") for item in files)
        assert results
        top_result = results[0]
        assert top_result["doc_type"] == "presentation"
        assert top_result["source"] == "plan_auto_ingest"
        assert top_result["summary"]
        assert top_result["match_reason"]
        assert top_result["matched_snippets"]
        assert "llm" in str(top_result.get("search_strategy") or "")

        print("Knowledge search smoke test passed.")
        print(f"Indexed files: {total}")
        print(f"Top result: {top_result['filename']}")
        print(f"Summary: {top_result['summary']}")


if __name__ == "__main__":
    asyncio.run(main())
