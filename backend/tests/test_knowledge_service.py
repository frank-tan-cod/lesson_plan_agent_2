from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.services import knowledge_service as knowledge_service_module
from backend.app.services.knowledge_service import IndexedEntry, KnowledgeService

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\x8d\x89\xb5\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FailingEmbeddingService:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


class FakeEmbeddingService:
    KEYWORDS = ("校园", "植物", "图片", "观察")

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [float(text.count(keyword)) for keyword in self.KEYWORDS]
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            vectors.append([item / norm for item in vector])
        return vectors


class FakeLLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
                )
            )
        )


class InMemoryVectorStore:
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


class KnowledgeServiceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    async def test_add_image_keeps_upload_when_indexing_fails(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FailingEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                image = await service.add_image(user_id, "campus.png", TINY_PNG, "校园植物观察图片")

                self.assertEqual(image.file_type, "image")
                self.assertFalse(bool(image.metadata_json.get("indexed")))
                self.assertTrue(Path(image.storage_path).exists())

                await service.delete_file(image.id, user_id=user_id)

    async def test_add_document_keeps_upload_when_indexing_fails(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FailingEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                document = await service.add_document(
                    user_id,
                    "lesson.md",
                    "# 校园植物\n观察校园植物的叶片变化并记录现象。".encode("utf-8"),
                )

                self.assertEqual(document.file_type, "document")
                self.assertFalse(bool(document.metadata_json.get("indexed")))
                self.assertTrue(Path(document.storage_path).exists())
                self.assertIn("校园植物", document.full_text or "")

                await service.delete_file(document.id, user_id=user_id)

    async def test_search_falls_back_to_keyword_matching(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FailingEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                image = await service.add_image(user_id, "campus.png", TINY_PNG, "校园植物观察图片")
                results = await service.search(user_id, "校园植物", top_k=5, file_type="image")

                self.assertTrue(results)
                self.assertEqual(results[0]["file_id"], image.id)
                self.assertEqual(results[0]["filename"], "campus.png")

                await service.delete_file(image.id, user_id=user_id)

    async def test_document_search_falls_back_to_keyword_matching(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FailingEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                document = await service.add_document(
                    user_id,
                    "lesson.md",
                    "# 校园植物\n观察校园植物的叶片变化并记录现象。".encode("utf-8"),
                )
                results = await service.search(user_id, "校园植物", top_k=5, file_type="document")

                self.assertTrue(results)
                self.assertEqual(results[0]["file_id"], document.id)
                self.assertEqual(results[0]["filename"], "lesson.md")

                await service.delete_file(document.id, user_id=user_id)

    async def test_answer_uses_grounded_document_context_and_citations(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FakeEmbeddingService(),
                    llm_client=FakeLLMClient(
                        {
                            "answer": "LangChain Agent 的入门示例可以先从环境安装、Agent 概念，再到最小可运行代码结构来讲。",
                            "cited_file_ids": ["doc-override"]
                        }
                    ),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                document = await service.add_document(
                    user_id,
                    "langchain-agent.md",
                    "# LangChain Agent\nLangChain Agent 入门示例包括环境安装、工具配置与最小可运行代码。".encode("utf-8"),
                    metadata_json={"source": "manual_upload"},
                )

                payload = await service.answer(user_id, "LangChain Agent 有什么入门示例？", top_k=5)

                self.assertTrue(payload["used_llm"])
                self.assertIn("LangChain Agent", payload["answer"])
                self.assertTrue(payload["results"])
                self.assertTrue(payload["citations"])
                self.assertEqual(payload["citations"][0]["file_id"], document.id)
                self.assertEqual(payload["citations"][0]["filename"], "langchain-agent.md")

                await service.delete_file(document.id, user_id=user_id)

    async def test_add_image_rejects_invalid_payload(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FakeEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                with self.assertRaisesRegex(ValueError, "可识别的图片"):
                    await service.add_image(user_id, "broken.png", b"not-an-image", "损坏图片")

    async def test_update_file_can_rename_describe_and_tag(self) -> None:
        user_id = f"test-{uuid.uuid4()}"
        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                service = KnowledgeService(
                    session,
                    embedding_service=FakeEmbeddingService(),
                    vector_store=InMemoryVectorStore(),
                    base_dir=temp_dir,
                )

                document = await service.add_document(
                    user_id,
                    "lesson.md",
                    "# 校园植物\n观察校园植物的叶片变化并记录现象。".encode("utf-8"),
                )

                updated = service.update_file(
                    document.id,
                    filename="植物观察快照.md",
                    description="用于对比编辑器保存的历史版本。",
                    tags=["回退点", "植物", "回退点"],
                    user_id=user_id,
                )

                self.assertIsNotNone(updated)
                assert updated is not None
                self.assertEqual(updated.filename, "植物观察快照.md")
                self.assertEqual(updated.description, "用于对比编辑器保存的历史版本。")
                self.assertEqual(updated.metadata_json.get("tags"), ["回退点", "植物"])


class KnowledgeServiceConfigurationTests(unittest.TestCase):
    def tearDown(self) -> None:
        knowledge_service_module._default_vector_store = None

    def test_default_vector_store_uses_shared_chroma_persist_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            shared_dir = Path(temp_dir) / "shared-chroma"
            with patch.object(
                knowledge_service_module,
                "settings",
                SimpleNamespace(CHROMA_PERSIST_DIR=str(shared_dir)),
            ):
                knowledge_service_module._default_vector_store = None

                store = knowledge_service_module.get_default_vector_store()

            self.assertEqual(Path(store.persist_directory), shared_dir)

    def test_default_vector_store_switches_when_base_dir_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            explicit_base_dir = Path(temp_dir) / "tenant-a"
            shared_dir = Path(temp_dir) / "shared-chroma"
            with patch.object(
                knowledge_service_module,
                "settings",
                SimpleNamespace(CHROMA_PERSIST_DIR=str(shared_dir)),
            ):
                knowledge_service_module._default_vector_store = None
                shared_store = knowledge_service_module.get_default_vector_store()
                explicit_store = knowledge_service_module.get_default_vector_store(explicit_base_dir)

            self.assertEqual(Path(shared_store.persist_directory), shared_dir)
            self.assertEqual(Path(explicit_store.persist_directory), explicit_base_dir / "chroma_data")


if __name__ == "__main__":
    unittest.main()
