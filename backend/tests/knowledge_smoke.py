"""Smoke test for the knowledge-base workflow without external model downloads."""

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
from backend.app.models import KnowledgeFile
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\x8d\x89\xb5\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeEmbeddingService:
    """Deterministic keyword-based embeddings for local tests."""

    KEYWORDS = ("光合作用", "植物", "图片", "校园")

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
    """Run an upload-search-delete smoke test."""
    init_db()

    document_id = ""
    image_id = ""
    file_ids: set[str] = set()

    with tempfile.TemporaryDirectory() as temp_dir:
        with session_maker() as session:
            service = KnowledgeService(
                session,
                embedding_service=FakeEmbeddingService(),
                vector_store=InMemoryVectorStore(),
                base_dir=temp_dir,
            )

            document = await service.add_document(
                "default",
                "lesson.md",
                "# 光合作用\n植物通过阳光完成光合作用，并在课堂上观察叶片变化。".encode("utf-8"),
            )
            image = await service.add_image(
                "default",
                "campus.png",
                TINY_PNG,
                "校园植物观察图片，适合讲解光合作用。",
            )
            document_id = document.id
            image_id = image.id

            files, total = service.list_files(user_id="default")
            file_ids = {item.id for item in files}
            search_results = await service.search("default", "光合作用", top_k=5)

            deleted = await service.delete_file(document_id, user_id="default")
            session.expire_all()
            removed_record = session.get(KnowledgeFile, document_id)

    assert total >= 2
    assert len(files) >= 2
    assert image_id in file_ids
    assert search_results
    assert search_results[0]["filename"] in {"lesson.md", "campus.png"}
    assert deleted is True
    assert removed_record is None

    print("Knowledge smoke test passed.")
    print(f"Indexed files: {total}")
    print(f"Top result: {search_results[0]}")


if __name__ == "__main__":
    asyncio.run(main())
