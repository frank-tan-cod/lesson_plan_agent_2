"""Smoke test for the search_knowledge tool without external model downloads."""

from __future__ import annotations

import asyncio
import math
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService
from backend.app.tools.knowledge_tools import register_knowledge_tools
from backend.app.tools.lesson_tools import register_lesson_tools
from backend.tools import ToolExecutor, ToolsRegistry


class FakeEmbeddingService:
    """Deterministic keyword-based embeddings for local tests."""

    KEYWORDS = ("惯性", "运动", "实验", "牛顿")

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
    """Upload a test document, execute the tool, and verify the returned snippet."""
    init_db()

    shared_vector_store = InMemoryVectorStore()
    fake_embedding_service = FakeEmbeddingService()
    created_file_ids: list[str] = []

    def service_factory(db: Any) -> KnowledgeService:
        return KnowledgeService(
            db,
            embedding_service=fake_embedding_service,
            vector_store=shared_vector_store,
            base_dir=temp_dir,
        )

    registry = ToolsRegistry()
    register_lesson_tools(registry)
    register_knowledge_tools(registry)
    executor = ToolExecutor(registry)

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            with session_maker() as session:
                service = service_factory(session)
                document = await service.add_document(
                    "default",
                    "inertia.md",
                    "# 惯性\n惯性实验说明：静止物体和运动物体都会保持原有状态，除非受到外力。".encode("utf-8"),
                )
                created_file_ids.append(document.id)

            with patch("backend.app.tools.knowledge_tools.KnowledgeService", side_effect=service_factory):
                result = await executor.execute(
                    "search_knowledge",
                    {"query": "参考我上传的惯性实验资料", "top_k": 3},
                )

            assert result["ok"] is True
            assert result["results"]
            assert result["results"][0]["filename"] == "inertia.md"
            assert "惯性实验说明" in result["results"][0]["text_snippet"]
            assert "inertia.md" in result["message"]
            assert "惯性实验说明" in result["message"]

            print("Knowledge tools smoke test passed.")
            print(result["message"])
        finally:
            for file_id in created_file_ids:
                with session_maker() as session:
                    await service_factory(session).delete_file(file_id, user_id="default")


if __name__ == "__main__":
    asyncio.run(main())
