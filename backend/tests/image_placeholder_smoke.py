"""Smoke test for image placeholder insertion and replacement."""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any
from unittest.mock import patch

from fastapi import UploadFile
from starlette.datastructures import Headers

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.routers.images import replace_image_placeholder
from backend.app.schemas import PlanCreate
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService
from backend.app.services.plan_service import PlanService
from backend.app.tools.lesson_tools import add_image_placeholder_tool

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\x8d\x89\xb5\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeEmbeddingService:
    """Deterministic keyword-based embeddings for local tests."""

    KEYWORDS = ("浮力", "实验", "图片", "演示")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [float(text.count(keyword)) for keyword in self.KEYWORDS]
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            vectors.append([item / norm for item in vector])
        return vectors


class InMemoryVectorStore:
    """Tiny in-memory vector store used by the smoke test."""

    def __init__(self) -> None:
        self._items: dict[str, list[IndexedEntry]] = {}

    def add_entries(self, user_id: str, entries: list[IndexedEntry]) -> None:
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
        ranked: list[tuple[float, IndexedEntry]] = []
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


async def main() -> None:
    """Run an end-to-end smoke test for image placeholders."""
    init_db()

    shared_vector_store = InMemoryVectorStore()
    plan_id = ""
    image_file_id = ""

    def service_factory(db: Any) -> KnowledgeService:
        return KnowledgeService(
            db,
            embedding_service=FakeEmbeddingService(),
            vector_store=shared_vector_store,
            base_dir=PROJECT_ROOT,
        )

    try:
        with session_maker() as session:
            plan = PlanService(session).create(
                PlanCreate(
                    title="图片占位符测试教案",
                    subject="物理",
                    grade="八年级",
                    content={
                        "sections": [
                            {
                                "type": "导入",
                                "content": "观察水中的木块。\n讨论木块为什么会上浮。",
                                "duration": 5,
                            },
                            {
                                "type": "新授",
                                "content": "通过实验总结浮力大小规律。",
                                "duration": 20,
                            },
                        ]
                    },
                )
            )
            plan_id = plan.id

        tool_result = add_image_placeholder_tool(
            plan_id=plan_id,
            section_type="导入",
            position="after_paragraph",
            description="浮力实验演示图",
            paragraph_index=0,
        )
        assert tool_result["ok"] is True
        assert tool_result["placeholder"] == "![图片：浮力实验演示图](upload_needed)"

        with session_maker() as session:
            updated_plan = PlanService(session).get(plan_id)
            assert updated_plan is not None
            section = updated_plan.content["sections"][0]
            assert "![图片：浮力实验演示图](upload_needed)" in section["content"]
            assert section["elements"][0]["type"] == "image_placeholder"

        file_obj = SpooledTemporaryFile()
        file_obj.write(TINY_PNG)
        file_obj.seek(0)
        upload = UploadFile(
            file=file_obj,
            filename="float.png",
            headers=Headers({"content-type": "image/png"}),
        )

        with session_maker() as session:
            with patch("backend.app.routers.images.get_knowledge_service", side_effect=service_factory):
                response = await replace_image_placeholder(
                    plan_id=plan_id,
                    description="浮力实验演示图",
                    file=upload,
                    db=session,
                )

        payload = response.model_dump()
        image_file_id = payload["file_id"]
        assert payload["url"].startswith("/uploads/images/")
        assert payload["replaced_sections"] == 1

        with session_maker() as session:
            replaced_plan = PlanService(session).get(plan_id)
            assert replaced_plan is not None
            content = replaced_plan.content["sections"][0]["content"]
            assert "upload_needed" not in content
            assert f"![浮力实验演示图]({payload['url']})" in content
            assert replaced_plan.content["sections"][0]["elements"][0]["status"] == "uploaded"

        print("Image placeholder smoke test passed.")
        print(f"Plan id: {plan_id}")
        print(f"Image url: {payload['url']}")
    finally:
        if image_file_id:
            with session_maker() as session:
                await KnowledgeService(
                    session,
                    embedding_service=FakeEmbeddingService(),
                    vector_store=shared_vector_store,
                    base_dir=PROJECT_ROOT,
                ).delete_file(image_file_id, user_id="default")
        if plan_id:
            with session_maker() as session:
                PlanService(session).delete(plan_id)


if __name__ == "__main__":
    asyncio.run(main())
