"""Smoke test for automatic lesson-plan ingestion into the knowledge base."""

from __future__ import annotations

import asyncio
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PlanCreate, SavepointCreate
from backend.app.services.export_service import ExportService
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService
from backend.app.services.plan_ingestion import auto_ingest_plan
from backend.app.services.plan_service import PlanService
from backend.app.services.savepoint_service import SavepointService


class FakeEmbeddingService:
    """Deterministic embedding provider for local smoke tests."""

    KEYWORDS = ("分数", "课堂", "练习", "讲解")

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
    """Run a basic automatic-ingestion workflow test."""
    init_db()

    with tempfile.TemporaryDirectory() as temp_dir:
        with session_maker() as session:
            knowledge_service = KnowledgeService(
                session,
                embedding_service=FakeEmbeddingService(),
                vector_store=InMemoryVectorStore(),
                base_dir=temp_dir,
            )
            plan_service = PlanService(session)
            savepoint_service = SavepointService(session)
            export_service = ExportService(plan_service)

            plan = plan_service.create(
                PlanCreate(
                    title="分数的初步认识",
                    subject="数学",
                    grade="三年级",
                    metadata={"semester": "上册"},
                    content={
                        "sections": [
                            {"title": "教学目标", "content": "理解几分之一的含义。"},
                            {"title": "课堂练习", "content": "- 观察图形\n- 口头表达分数"},
                        ]
                    },
                )
            )

            export_bytes = export_service.export_to_docx(plan.id)
            assert export_bytes[:2] == b"PK"

            first_ingest = await auto_ingest_plan(plan.id, session, knowledge_service, trigger="export")
            duplicate_export = await auto_ingest_plan(plan.id, session, knowledge_service, trigger="export")

            savepoint = savepoint_service.create(
                SavepointCreate(
                    plan_id=plan.id,
                    label="第一次保存",
                    snapshot={
                        "sections": [
                            {"title": "教学目标", "content": "理解几分之一与几分之几。"},
                            {"title": "课堂练习", "content": "- 动手折纸\n- 说出对应分数"},
                        ]
                    },
                )
            )

            savepoint_ingest = await auto_ingest_plan(
                plan.id,
                session,
                knowledge_service,
                content_override=savepoint.snapshot,
                trigger="savepoint",
                version=f"savepoint:{savepoint.id}",
                version_timestamp=savepoint.created_at,
            )

            files, total = knowledge_service.list_files(user_id="default")
            filenames = {item.filename for item in files}
            metadata_versions = {item.metadata_json.get("version") for item in files}

    assert first_ingest is not None
    assert duplicate_export is None
    assert savepoint_ingest is not None
    assert total >= 2
    assert any(name.endswith(".md") for name in filenames)
    assert any(version and str(version).startswith("plan:") for version in metadata_versions)
    assert f"savepoint:{savepoint.id}" in metadata_versions

    print("Plan ingestion smoke test passed.")
    print(f"Indexed documents: {total}")
    print(f"Versions: {sorted(str(item) for item in metadata_versions if item)}")


if __name__ == "__main__":
    asyncio.run(main())
