"""Smoke test for saving an editor snapshot into the knowledge base."""

from __future__ import annotations

import asyncio
import math
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.routers.savepoints import create_savepoint
from backend.app.schemas import PlanCreate, SavepointCreate
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService
from backend.app.services.plan_service import PlanService


class FakeEmbeddingService:
    """Deterministic embedding provider for markdown snapshots."""

    KEYWORDS = ("快照", "浮力", "实验", "回退点")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [float(text.count(keyword)) for keyword in self.KEYWORDS]
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            vectors.append([item / norm for item in vector])
        return vectors


class InMemoryVectorStore:
    """Small in-memory store used to avoid external dependencies."""

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
    ) -> list[dict]:
        return []

    def delete_file(self, user_id: str, file_id: str) -> None:
        bucket = self._items.get(user_id, [])
        self._items[user_id] = [item for item in bucket if item.metadata.get("file_id") != file_id]


async def main() -> None:
    """Ensure save-to-knowledge creates both a savepoint and a searchable knowledge file."""
    init_db()

    with tempfile.TemporaryDirectory() as temp_dir:
        with session_maker() as session:
            plan_service = PlanService(session)
            plan = plan_service.create(
                PlanCreate(
                    title="浮力实验教案",
                    subject="物理",
                    grade="八年级",
                    content={"sections": [{"type": "导入", "content": "观察鸡蛋在盐水中的沉浮变化。", "duration": 8}]},
                )
            )

            knowledge_service = KnowledgeService(
                session,
                embedding_service=FakeEmbeddingService(),
                vector_store=InMemoryVectorStore(),
                base_dir=temp_dir,
            )

            with patch("backend.app.routers.savepoints.KnowledgeService", new=lambda db, user_id: knowledge_service):
                savepoint = await create_savepoint(
                    SavepointCreate(
                        plan_id=plan.id,
                        label="实验版初稿",
                        snapshot={"sections": [{"type": "导入", "content": "先观察鸡蛋沉浮，再解释浮力。", "duration": 8}]},
                        persist_to_knowledge=True,
                        knowledge_title="浮力实验-当前快照",
                        knowledge_description="适合回看实验导入版本。",
                        knowledge_tags=["回退点", "实验", "浮力"],
                    ),
                    db=session,
                    user_id="default",
                )

            files, total = knowledge_service.list_files(user_id="default")

    assert savepoint.id
    assert total >= 1
    matching_file = next(
        (
            item
            for item in files
            if item.metadata_json.get("source") == "editor_snapshot"
            and item.metadata_json.get("savepoint_id") == savepoint.id
        ),
        None,
    )
    assert matching_file is not None
    assert matching_file.metadata_json.get("tags") == ["回退点", "实验", "浮力"]
    assert matching_file.description == "适合回看实验导入版本。"

    print("Savepoint knowledge smoke test passed.")
    print(f"Knowledge filename: {matching_file.filename}")
    print(f"Savepoint id: {savepoint.id}")


if __name__ == "__main__":
    asyncio.run(main())
