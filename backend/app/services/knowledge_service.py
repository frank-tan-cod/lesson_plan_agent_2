"""Knowledge-base service for uploads, indexing, retrieval, and cleanup."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.settings import settings
from ..models import KnowledgeFile
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .document_parser import parse_document, split_text
from .embedding_service import EmbeddingService

logger = logging.getLogger(__name__)
DOCUMENT_FILE_TYPE = "document"
IMAGE_FILE_TYPE = "image"
SUPPORTED_DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown"}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_SEARCH_CANDIDATES = 24
MANUAL_UPLOAD_SOURCE = "manual_upload"
EDITOR_SNAPSHOT_SOURCE = "editor_snapshot"


@dataclass(slots=True)
class IndexedEntry:
    """A single vector-store record."""

    id: str
    text: str
    metadata: dict[str, Any]
    embedding: list[float]


class VectorStoreProtocol(Protocol):
    """Minimal interface required by the knowledge service."""

    def add_entries(self, user_id: str, entries: list[IndexedEntry]) -> None:
        ...

    def query(
        self,
        user_id: str,
        query_embedding: list[float],
        *,
        top_k: int,
        file_type: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def delete_file(self, user_id: str, file_id: str) -> None:
        ...


class ChromaKnowledgeStore:
    """Chroma-backed vector storage."""

    def __init__(self, persist_directory: str | Path) -> None:
        self.persist_directory = Path(persist_directory)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self._client: Any | None = None

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
            except ImportError as exc:  # pragma: no cover - depends on optional package
                raise RuntimeError("缺少 chromadb 依赖，无法使用知识库向量检索。") from exc

            self._client = chromadb.PersistentClient(path=str(self.persist_directory))
        return self._client

    def _get_collection(self, user_id: str):
        collection_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"user_{user_id}_docs").strip("_") or "user_default_docs"
        return self._get_client().get_or_create_collection(name=collection_name)

    def add_entries(self, user_id: str, entries: list[IndexedEntry]) -> None:
        """Persist records into Chroma."""
        if not entries:
            return

        collection = self._get_collection(user_id)
        collection.add(
            ids=[item.id for item in entries],
            documents=[item.text for item in entries],
            metadatas=[item.metadata for item in entries],
            embeddings=[item.embedding for item in entries],
        )

    def query(
        self,
        user_id: str,
        query_embedding: list[float],
        *,
        top_k: int,
        file_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query similar entries from Chroma."""
        collection = self._get_collection(user_id)
        params: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if file_type:
            params["where"] = {"file_type": file_type}

        raw_result = collection.query(**params)
        documents = (raw_result.get("documents") or [[]])[0]
        metadatas = (raw_result.get("metadatas") or [[]])[0]
        distances = (raw_result.get("distances") or [[]])[0]

        results: list[dict[str, Any]] = []
        for index, document in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = distances[index] if index < len(distances) else None
            results.append({"text": document or "", "metadata": metadata or {}, "distance": distance})
        return results

    def delete_file(self, user_id: str, file_id: str) -> None:
        """Delete all vectors related to one uploaded file."""
        collection = self._get_collection(user_id)
        collection.delete(where={"file_id": file_id})


_default_vector_store: VectorStoreProtocol | None = None


def _resolve_chroma_persist_dir(base_dir: str | Path | None = None) -> Path:
    """Resolve the Chroma persistence directory for knowledge vectors."""
    if base_dir is not None:
        return Path(base_dir) / "chroma_data"
    return Path(settings.CHROMA_PERSIST_DIR)


def get_default_vector_store(base_dir: str | Path | None = None) -> VectorStoreProtocol:
    """Return the shared Chroma vector store."""
    global _default_vector_store
    persist_dir = _resolve_chroma_persist_dir(base_dir)
    current_dir = getattr(_default_vector_store, "persist_directory", None)
    if _default_vector_store is None or current_dir != str(persist_dir):
        _default_vector_store = ChromaKnowledgeStore(persist_dir)
    return _default_vector_store


def initialize_knowledge_resources(base_dir: str | Path | None = None) -> None:
    """Create local directories and eagerly prepare the vector-store path."""
    root = Path(base_dir or PROJECT_ROOT)
    (root / "uploads" / "documents").mkdir(parents=True, exist_ok=True)
    (root / "uploads" / "images").mkdir(parents=True, exist_ok=True)
    _resolve_chroma_persist_dir(base_dir).mkdir(parents=True, exist_ok=True)

    try:
        get_default_vector_store(base_dir)
    except RuntimeError as exc:  # pragma: no cover - optional dependency
        logger.warning("Knowledge store is not ready yet: %s", exc)


class KnowledgeService:
    """Coordinate file persistence, vector indexing, and semantic search."""

    def __init__(
        self,
        db: Session,
        *,
        user_id: str = DEFAULT_USER_ID,
        embedding_service: EmbeddingService | Any | None = None,
        llm_client: Any | None = None,
        vector_store: VectorStoreProtocol | None = None,
        base_dir: str | Path | None = None,
        default_user_id: str | None = None,
    ) -> None:
        self.db = db
        self.base_dir = Path(base_dir or PROJECT_ROOT)
        self._vector_store_base_dir = Path(base_dir) if base_dir is not None else None
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
        self.default_user_id = resolve_user_id(default_user_id, self.user_id)
        self._embedding_service = embedding_service
        self._llm_client = llm_client
        self._vector_store = vector_store
        self.documents_dir = self.base_dir / "uploads" / "documents"
        self.images_dir = self.base_dir / "uploads" / "images"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)

    @property
    def embedding_service(self) -> EmbeddingService | Any:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def vector_store(self) -> VectorStoreProtocol:
        if self._vector_store is None:
            self._vector_store = get_default_vector_store(self._vector_store_base_dir)
        return self._vector_store

    @property
    def llm_client(self) -> Any | None:
        if self._llm_client is None:
            api_key = settings.DEEPSEEK_API_KEY or settings.SUMMARY_API_KEY
            if not api_key:
                return None
            try:
                from openai import OpenAI
            except ImportError:  # pragma: no cover - depends on optional package
                logger.warning("缺少 openai 依赖，跳过知识库 LLM 结果整理。")
                return None
            self._llm_client = OpenAI(
                api_key=api_key,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        return self._llm_client

    def list_files(
        self,
        *,
        user_id: str | None = None,
        skip: int = 0,
        limit: int = 100,
        file_type: str | None = None,
    ) -> tuple[list[KnowledgeFile], int]:
        """List uploaded knowledge files."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        self._validate_file_type(file_type)

        stmt = select(KnowledgeFile).where(KnowledgeFile.user_id == resolved_user_id)
        total_stmt = select(func.count()).select_from(KnowledgeFile).where(KnowledgeFile.user_id == resolved_user_id)

        if file_type:
            stmt = stmt.where(KnowledgeFile.file_type == file_type)
            total_stmt = total_stmt.where(KnowledgeFile.file_type == file_type)

        stmt = stmt.order_by(KnowledgeFile.created_at.desc()).offset(skip).limit(limit)
        items = list(self.db.execute(stmt).scalars().all())
        total = int(self.db.execute(total_stmt).scalar_one())
        return items, total

    def get_file(self, file_id: str, *, user_id: str | None = None) -> KnowledgeFile | None:
        """Fetch one knowledge file if it belongs to the resolved user."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        return self.db.execute(
            select(KnowledgeFile).where(KnowledgeFile.id == file_id, KnowledgeFile.user_id == resolved_user_id)
        ).scalar_one_or_none()

    def get_file_content(self, file_id: str, *, user_id: str | None = None) -> str:
        """Return the stored full text for a document, reparsing the source file if needed."""
        record = self.get_file(file_id, user_id=user_id)
        if record is None:
            raise ValueError("知识库文件不存在。")
        if record.file_type != DOCUMENT_FILE_TYPE:
            return (record.description or "").strip()

        if record.full_text and record.full_text.strip():
            return record.full_text.strip()

        storage_path = Path(record.storage_path)
        if not storage_path.exists():
            return ""

        try:
            text, _ = parse_document(storage_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to parse document %s: %s", file_id, exc)
            return ""

        normalized_text = text.strip()
        if not normalized_text:
            return ""

        record.full_text = normalized_text
        try:
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            logger.warning("Failed to persist full_text for document %s.", file_id)
        else:
            self.db.refresh(record)
        return normalized_text

    async def add_document(
        self,
        user_id: str,
        file_name: str,
        file_bytes: bytes,
        *,
        metadata_json: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> KnowledgeFile:
        """Upload, parse, chunk, embed, and index a document."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        original_name = self._clean_filename(file_name)
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise ValueError("仅支持 PDF、DOCX、Markdown 文档。")

        normalized_description = (description or "").strip() or None
        file_id = str(uuid.uuid4())
        storage_path = self.documents_dir / f"{file_id}{extension}"
        self._write_bytes(storage_path, file_bytes)

        try:
            text, metadata = parse_document(storage_path)
            chunks = split_text(text)
            if not chunks:
                raise ValueError("文档中未提取到可用文本。")

            indexed = self._try_index_document(
                file_id=file_id,
                user_id=resolved_user_id,
                filename=original_name,
                chunks=chunks,
            )

            knowledge_file = KnowledgeFile(
                id=file_id,
                user_id=resolved_user_id,
                filename=original_name,
                file_type=DOCUMENT_FILE_TYPE,
                storage_path=str(storage_path),
                description=normalized_description,
                full_text=text,
                metadata_json={
                    "source": MANUAL_UPLOAD_SOURCE,
                    "trigger": MANUAL_UPLOAD_SOURCE,
                    **metadata,
                    **(metadata_json or {}),
                    "chunk_count": len(chunks),
                    "extension": extension,
                    "size_bytes": len(file_bytes),
                    "indexed": indexed,
                },
            )

            self.db.add(knowledge_file)
            self.db.commit()
            self.db.refresh(knowledge_file)
            return knowledge_file
        except Exception:
            self.db.rollback()
            self._safe_delete_vectors(resolved_user_id, file_id)
            self._safe_unlink(storage_path)
            raise

    async def add_image(self, user_id: str, file_name: str, file_bytes: bytes, description: str) -> KnowledgeFile:
        """Upload an image and index its description for retrieval."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        original_name = self._clean_filename(file_name)
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError("仅支持常见图片格式上传。")

        normalized_description = description.strip()
        if not normalized_description:
            raise ValueError("图片简介不能为空。")

        file_id = str(uuid.uuid4())
        storage_path = self.images_dir / f"{file_id}{extension}"
        self._write_bytes(storage_path, file_bytes)

        try:
            metadata = self._extract_image_metadata(storage_path)
            indexed = self._try_index_image(
                file_id=file_id,
                user_id=resolved_user_id,
                filename=original_name,
                description=normalized_description,
            )

            knowledge_file = KnowledgeFile(
                id=file_id,
                user_id=resolved_user_id,
                filename=original_name,
                file_type=IMAGE_FILE_TYPE,
                storage_path=str(storage_path),
                description=normalized_description,
                full_text=None,
                metadata_json={
                    "source": MANUAL_UPLOAD_SOURCE,
                    "trigger": MANUAL_UPLOAD_SOURCE,
                    **metadata,
                    "extension": extension,
                    "size_bytes": len(file_bytes),
                    "indexed": indexed,
                },
            )

            self.db.add(knowledge_file)
            self.db.commit()
            self.db.refresh(knowledge_file)
            return knowledge_file
        except Exception:
            self.db.rollback()
            self._safe_delete_vectors(resolved_user_id, file_id)
            self._safe_unlink(storage_path)
            raise

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 5,
        file_type: str | None = None,
        enable_llm_rerank: bool = True,
    ) -> list[dict[str, Any]]:
        """Run hybrid retrieval, aggregate by file, and optionally organize results with an LLM."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        normalized_query = query.strip()
        if not normalized_query:
            return []

        self._validate_file_type(file_type)
        candidate_limit = min(max(top_k * 4, top_k), MAX_SEARCH_CANDIDATES)
        semantic_matches = self._semantic_search(
            resolved_user_id,
            normalized_query,
            top_k=candidate_limit,
            file_type=file_type,
        )
        keyword_records = self._keyword_search_records(
            resolved_user_id,
            normalized_query,
            top_k=candidate_limit,
            file_type=file_type,
        )
        if not semantic_matches and not keyword_records:
            return []

        ranked_results = self._build_ranked_results(
            user_id=resolved_user_id,
            query=normalized_query,
            semantic_matches=semantic_matches,
            keyword_records=keyword_records,
        )
        if not ranked_results:
            return []

        if enable_llm_rerank:
            llm_ranked = self._organize_results_with_llm(normalized_query, ranked_results, top_k=top_k)
            if llm_ranked:
                return llm_ranked[:top_k]

        return ranked_results[:top_k]

    async def answer(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 5,
        file_type: str | None = None,
        enable_llm_rerank: bool = True,
    ) -> dict[str, Any]:
        """Answer a user question with grounded knowledge-base evidence."""
        normalized_query = query.strip()
        if not normalized_query:
            return {
                "answer": "",
                "citations": [],
                "results": [],
                "used_llm": False,
            }

        results = await self.search(
            user_id,
            normalized_query,
            top_k=top_k,
            file_type=file_type,
            enable_llm_rerank=enable_llm_rerank,
        )
        if not results:
            return {
                "answer": "知识库里暂时没有找到足够相关的资料。可以换个关键词，或先把相关教案、PPT、图片放进知识库。",
                "citations": [],
                "results": [],
                "used_llm": False,
            }

        contexts = self._build_answer_contexts(user_id, results)
        llm_payload = self._answer_with_llm(normalized_query, contexts)
        if llm_payload:
            citation_ids = [str(item) for item in llm_payload.get("cited_file_ids") or [] if str(item).strip()]
            citations = self._select_citations(results, citation_ids)
            if not citations:
                citations = self._select_citations(results, [])
            answer = str(llm_payload.get("answer") or "").strip()
            if answer:
                return {
                    "answer": answer,
                    "citations": citations,
                    "results": results,
                    "used_llm": True,
                }

        return {
            "answer": self._build_fallback_answer(normalized_query, results),
            "citations": self._select_citations(results, []),
            "results": results,
            "used_llm": False,
        }

    async def delete_file(self, file_id: str, *, user_id: str | None = None) -> bool:
        """Delete file metadata, local storage, and indexed vectors."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        record = self.db.get(KnowledgeFile, file_id)
        if record is None:
            return False
        if record.user_id != resolved_user_id:
            return False

        self._safe_delete_vectors(record.user_id, record.id)
        self._safe_unlink(Path(record.storage_path))

        try:
            self.db.delete(record)
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("删除知识库文件失败。") from exc
        return True

    def update_file(
        self,
        file_id: str,
        *,
        filename: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        user_id: str | None = None,
    ) -> KnowledgeFile | None:
        """Update editable knowledge-file fields."""
        resolved_user_id = resolve_user_id(user_id, self.default_user_id)
        record = self.get_file(file_id, user_id=resolved_user_id)
        if record is None:
            return None

        if filename is not None:
            record.filename = self._clean_filename(filename)
        if description is not None:
            record.description = description.strip() or None
        if tags is not None:
            metadata = dict(self._ensure_dict(record.metadata_json))
            normalized_tags = self._normalize_tags(tags)
            if normalized_tags:
                metadata["tags"] = normalized_tags
            else:
                metadata.pop("tags", None)
            record.metadata_json = metadata

        try:
            self.db.commit()
            self.db.refresh(record)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("更新知识库文件失败。") from exc
        return record

    def _clean_filename(self, file_name: str) -> str:
        filename = Path(file_name or "").name
        if not filename:
            raise ValueError("文件名不能为空。")
        return filename

    def _validate_file_type(self, file_type: str | None) -> None:
        if file_type is None:
            return
        if file_type not in {DOCUMENT_FILE_TYPE, IMAGE_FILE_TYPE}:
            raise ValueError("file_type 仅支持 document 或 image。")

    def get_file_tags(self, record: KnowledgeFile) -> list[str]:
        """Return normalized user-facing tags from metadata."""
        return self._normalize_tags(self._ensure_dict(record.metadata_json).get("tags"))

    def _write_bytes(self, target_path: Path, payload: bytes) -> None:
        if not payload:
            raise ValueError("上传文件不能为空。")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)

    def _extract_image_metadata(self, file_path: Path) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            from PIL import Image
            from PIL import UnidentifiedImageError
        except ImportError:  # pragma: no cover - depends on optional package
            logger.warning("Pillow 未安装，图片元数据将被跳过。")
            return metadata

        try:
            with Image.open(file_path) as image:
                metadata["width"] = image.width
                metadata["height"] = image.height
                metadata["format"] = image.format
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("上传的文件不是可识别的图片。") from exc
        return metadata

    def _try_index_image(self, *, file_id: str, user_id: str, filename: str, description: str) -> bool:
        try:
            embeddings = self.embedding_service.embed([description])
            if not embeddings:
                raise RuntimeError("图片简介向量化失败。")

            entry = IndexedEntry(
                id=f"{file_id}:image:0",
                text=description,
                metadata={
                    "file_id": file_id,
                    "source_file_id": file_id,
                    "filename": filename,
                    "file_type": IMAGE_FILE_TYPE,
                    "chunk_index": 0,
                    "user_id": user_id,
                },
                embedding=embeddings[0],
            )
            self.vector_store.add_entries(user_id, [entry])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Image upload kept without semantic index for %s: %s", file_id, exc)
            return False
        return True

    def _try_index_document(
        self,
        *,
        file_id: str,
        user_id: str,
        filename: str,
        chunks: list[dict[str, Any]],
    ) -> bool:
        try:
            chunk_texts = [str(item.get("text") or "") for item in chunks]
            embeddings = self.embedding_service.embed(chunk_texts)
            if len(embeddings) != len(chunks):
                raise RuntimeError("向量化结果数量与切片数量不一致。")

            entries = [
                IndexedEntry(
                    id=f"{file_id}:chunk:{chunk['index']}",
                    text=str(chunk["text"]),
                    metadata={
                        "file_id": file_id,
                        "source_file_id": file_id,
                        "filename": filename,
                        "file_type": DOCUMENT_FILE_TYPE,
                        "chunk_index": chunk["index"],
                        "user_id": user_id,
                    },
                    embedding=embedding,
                )
                for chunk, embedding in zip(chunks, embeddings)
            ]
            self.vector_store.add_entries(user_id, entries)
        except Exception as exc:  # noqa: BLE001
            self._safe_delete_vectors(user_id, file_id)
            logger.warning("Document upload kept without semantic index for %s: %s", file_id, exc)
            return False
        return True

    def _semantic_search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int,
        file_type: str | None,
    ) -> list[dict[str, Any]]:
        try:
            query_embedding = self.embedding_service.embed([query])[0]
            return self.vector_store.query(
                user_id,
                query_embedding,
                top_k=top_k,
                file_type=file_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knowledge semantic retrieval unavailable, fallback to keyword search: %s", exc)
            return []

    def _keyword_search_records(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int,
        file_type: str | None,
    ) -> list[KnowledgeFile]:
        terms = self._keyword_terms(query)
        stmt = select(KnowledgeFile).where(KnowledgeFile.user_id == user_id)
        if file_type:
            stmt = stmt.where(KnowledgeFile.file_type == file_type)
        records = list(
            self.db.execute(stmt.order_by(KnowledgeFile.created_at.desc()).limit(max(top_k * 12, 120))).scalars().all()
        )
        ranked = sorted(
            records,
            key=lambda record: self._keyword_match_score(record, query, terms),
            reverse=True,
        )
        return [record for record in ranked if self._keyword_match_score(record, query, terms) > 0][:top_k]

    def _build_ranked_results(
        self,
        *,
        user_id: str,
        query: str,
        semantic_matches: list[dict[str, Any]],
        keyword_records: list[KnowledgeFile],
    ) -> list[dict[str, Any]]:
        file_ids = {
            metadata.get("file_id")
            for item in semantic_matches
            if isinstance((metadata := item.get("metadata")), dict) and metadata.get("file_id")
        }
        file_ids.update(record.id for record in keyword_records)
        file_ids.discard(None)
        if not file_ids:
            return []

        records = self.db.execute(
            select(KnowledgeFile).where(
                KnowledgeFile.id.in_(file_ids),
                KnowledgeFile.user_id == user_id,
            )
        ).scalars().all()
        record_map = {record.id: record for record in records}
        if not record_map:
            return []

        buckets: dict[str, dict[str, Any]] = {}
        query_terms = self._keyword_terms(query)

        for item in semantic_matches:
            metadata = item.get("metadata") or {}
            file_id = metadata.get("file_id")
            record = record_map.get(file_id)
            if record is None:
                continue

            bucket = self._ensure_result_bucket(buckets, record)
            score = self._distance_to_score(item.get("distance"))
            bucket["semantic_score"] = max(float(bucket["semantic_score"]), score)
            bucket["semantic_hits"] = int(bucket["semantic_hits"]) + 1
            self._append_unique_snippet(bucket["matched_snippets"], self._trim_snippet(item.get("text") or ""))

        for record in keyword_records:
            bucket = self._ensure_result_bucket(buckets, record)
            raw_keyword_score = self._keyword_match_score(record, query, query_terms)
            bucket["keyword_score"] = max(float(bucket["keyword_score"]), self._normalize_keyword_score(raw_keyword_score))
            self._append_unique_snippet(
                bucket["matched_snippets"],
                self._build_keyword_snippet(record, query, query_terms),
            )

        results: list[dict[str, Any]] = []
        for file_id, bucket in buckets.items():
            record = record_map.get(file_id)
            if record is None:
                continue

            semantic_score = float(bucket["semantic_score"])
            keyword_score = float(bucket["keyword_score"])
            semantic_hits = int(bucket["semantic_hits"])
            relevance_score = semantic_score * 0.68 + keyword_score * 0.32
            relevance_score += min(0.08, max(semantic_hits - 1, 0) * 0.02)
            relevance_score = round(min(max(relevance_score, 0.0), 0.999999), 6)
            strategy_parts = []
            if semantic_score > 0:
                strategy_parts.append("semantic")
            if keyword_score > 0:
                strategy_parts.append("keyword")
            search_strategy = "+".join(strategy_parts) or "keyword"

            metadata_json = self._ensure_dict(record.metadata_json)
            snippets = [snippet for snippet in bucket["matched_snippets"] if snippet][:3]
            text_snippet = snippets[0] if snippets else self._fallback_snippet(record)
            summary = self._build_result_summary(record)
            results.append(
                {
                    "file_id": record.id,
                    "filename": record.filename,
                    "file_type": record.file_type,
                    "text_snippet": text_snippet,
                    "matched_snippets": snippets or ([text_snippet] if text_snippet else []),
                    "relevance_score": relevance_score,
                    "summary": summary,
                    "match_reason": self._build_match_reason(
                        record=record,
                        semantic_score=semantic_score,
                        keyword_score=keyword_score,
                        semantic_hits=semantic_hits,
                    ),
                    "source": metadata_json.get("source"),
                    "trigger": metadata_json.get("trigger"),
                    "doc_type": metadata_json.get("doc_type"),
                    "search_strategy": search_strategy,
                }
            )

        results.sort(
            key=lambda item: (
                float(item.get("relevance_score") or 0.0),
                1 if item.get("file_type") == DOCUMENT_FILE_TYPE else 0,
                item.get("filename") or "",
            ),
            reverse=True,
        )
        return results

    def _ensure_result_bucket(self, buckets: dict[str, dict[str, Any]], record: KnowledgeFile) -> dict[str, Any]:
        existing = buckets.get(record.id)
        if existing is not None:
            return existing

        bucket = {
            "semantic_score": 0.0,
            "keyword_score": 0.0,
            "semantic_hits": 0,
            "matched_snippets": [],
        }
        buckets[record.id] = bucket
        return bucket

    def _organize_results_with_llm(
        self,
        query: str,
        ranked_results: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        client = self.llm_client
        if client is None or not ranked_results:
            return []

        candidates = ranked_results[: min(max(top_k * 2, top_k), 8)]
        prompt = self._build_llm_search_prompt(query, candidates)
        try:
            response = client.chat.completions.create(
                model=settings.INTENT_MODEL_NAME or settings.MODEL_NAME,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是知识库检索整理助手。"
                            "只允许根据候选结果做重排、概括和匹配原因说明，不允许编造未提供的内容。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                stream=False,
            )
            raw_content = response.choices[0].message.content or "{}"
            payload = json.loads(raw_content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识库 LLM 检索整理失败，回退到启发式排序: %s", exc)
            return []

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return []

        llm_map: dict[str, dict[str, Any]] = {}
        llm_order: list[str] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue
            llm_map[file_id] = item
            llm_order.append(file_id)

        if not llm_map:
            return []

        heuristic_map = {item["file_id"]: dict(item) for item in ranked_results}
        merged_results: list[dict[str, Any]] = []
        seen_file_ids: set[str] = set()
        for file_id in llm_order:
            base = heuristic_map.get(file_id)
            if base is None:
                continue
            merged_results.append(self._merge_llm_result(base, llm_map[file_id]))
            seen_file_ids.add(file_id)

        for item in ranked_results:
            if item["file_id"] in seen_file_ids:
                continue
            merged_results.append(item)

        return merged_results

    def _build_llm_search_prompt(self, query: str, candidates: list[dict[str, Any]]) -> str:
        serialized = json.dumps(candidates, ensure_ascii=False, indent=2)
        return f"""请根据用户查询，对候选知识库结果做语义重排和整理。

用户查询：
{query}

候选结果：
{serialized}

要求：
1. 只能使用候选结果里已有的信息。
2. 优先返回最能支撑用户后续创作/参考的资料。
3. 对每条结果补一条简洁 summary 和 match_reason。
4. `relevance_score` 使用 0 到 1 的小数。
5. 只输出 JSON，对象结构必须为：
{{
  "results": [
    {{
      "file_id": "候选里的 file_id",
      "relevance_score": 0.91,
      "summary": "一句话概括这个资料可提供什么参考",
      "match_reason": "一句话说明它为什么和查询相关"
    }}
  ]
}}"""

    def _merge_llm_result(self, base: dict[str, Any], llm_item: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        llm_score = self._safe_float(llm_item.get("relevance_score"))
        if llm_score is not None:
            heuristic_score = float(base.get("relevance_score") or 0.0)
            merged["relevance_score"] = round(min(max((heuristic_score + llm_score) / 2, 0.0), 0.999999), 6)
        if str(llm_item.get("summary") or "").strip():
            merged["summary"] = str(llm_item.get("summary")).strip()
        if str(llm_item.get("match_reason") or "").strip():
            merged["match_reason"] = str(llm_item.get("match_reason")).strip()
        strategy = str(base.get("search_strategy") or "").strip()
        merged["search_strategy"] = f"{strategy}+llm" if strategy else "llm"
        return merged

    def _build_answer_contexts(self, user_id: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        for item in results[:4]:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id:
                continue
            try:
                body = self.get_file_content(file_id, user_id=user_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取知识库文件正文失败，file_id=%s: %s", file_id, exc)
                body = ""

            content_excerpt = self._trim_snippet(body, limit=1800)
            contexts.append(
                {
                    "file_id": file_id,
                    "filename": item.get("filename"),
                    "file_type": item.get("file_type"),
                    "summary": item.get("summary"),
                    "match_reason": item.get("match_reason"),
                    "text_snippet": item.get("text_snippet"),
                    "matched_snippets": item.get("matched_snippets") or [],
                    "search_strategy": item.get("search_strategy"),
                    "content_excerpt": content_excerpt,
                }
            )
        return contexts

    def _answer_with_llm(self, query: str, contexts: list[dict[str, Any]]) -> dict[str, Any] | None:
        client = self.llm_client
        if client is None or not contexts:
            return None

        prompt = self._build_llm_answer_prompt(query, contexts)
        try:
            response = client.chat.completions.create(
                model=settings.INTENT_MODEL_NAME or settings.MODEL_NAME,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是知识库问答助手。"
                            "必须严格依据给定资料回答；如果资料不足，要明确说明不足。"
                            "不要编造未在资料中出现的事实。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                stream=False,
            )
            raw_content = response.choices[0].message.content or "{}"
            payload = json.loads(raw_content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识库 LLM 问答失败，回退到规则回答: %s", exc)
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    def _build_llm_answer_prompt(self, query: str, contexts: list[dict[str, Any]]) -> str:
        serialized = json.dumps(contexts, ensure_ascii=False, indent=2)
        return f"""请根据知识库检索到的资料，回答用户问题。

用户问题：
{query}

候选资料：
{serialized}

要求：
1. 只能使用候选资料里的信息回答。
2. 优先给出对用户最有帮助的自然语言结论，而不是简单罗列文件名。
3. 如果资料不足以完整回答，要明确指出还缺什么。
4. `cited_file_ids` 只填写实际引用到的 file_id。
5. 只输出 JSON，结构必须为：
{{
  "answer": "自然语言回答",
  "cited_file_ids": ["file_id_1", "file_id_2"]
}}"""

    def _select_citations(self, results: list[dict[str, Any]], preferred_ids: list[str]) -> list[dict[str, Any]]:
        result_map = {str(item.get("file_id") or ""): item for item in results}
        citations: list[dict[str, Any]] = []
        seen: set[str] = set()

        for file_id in preferred_ids:
            item = result_map.get(file_id)
            if item is None or file_id in seen:
                continue
            citations.append(self._build_citation(item))
            seen.add(file_id)

        if citations:
            return citations[:3]

        for item in results[:3]:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id or file_id in seen:
                continue
            citations.append(self._build_citation(item))
            seen.add(file_id)
        return citations

    def _build_citation(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_id": str(item.get("file_id") or ""),
            "filename": str(item.get("filename") or ""),
            "file_type": str(item.get("file_type") or ""),
            "text_snippet": str(item.get("text_snippet") or ""),
            "summary": item.get("summary"),
            "match_reason": item.get("match_reason"),
            "source": item.get("source"),
            "trigger": item.get("trigger"),
            "doc_type": item.get("doc_type"),
            "relevance_score": float(item.get("relevance_score") or 0.0),
        }

    def _build_fallback_answer(self, query: str, results: list[dict[str, Any]]) -> str:
        top_results = results[:3]
        lines = [f"我先根据知识库里最相关的资料回答“{query}”。"]
        primary = top_results[0] if top_results else None
        if primary is not None:
            summary = str(primary.get("summary") or "").strip()
            snippet = str(primary.get("text_snippet") or "").strip()
            if summary:
                lines.append(f"最相关的是《{primary.get('filename') or '未命名资料'}》，它主要提供：{summary}")
            if snippet:
                lines.append(f"从命中内容看，关键依据是：{snippet}")

        if len(top_results) > 1:
            references = "；".join(
                f"《{item.get('filename') or '未命名资料'}》"
                for item in top_results[1:]
            )
            lines.append(f"另外还可以参考 {references}，补充不同角度的细节。")

        lines.append("如果你希望，我可以继续基于这些资料整理成更完整的讲解、提纲或可直接复用的内容。")
        return "\n".join(lines)

    def _keyword_terms(self, query: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", query.strip())
        if not normalized:
            return []

        terms: list[str] = [normalized]
        lowered = normalized.lower()
        terms.extend(re.findall(r"[a-z0-9_]+", lowered))

        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            terms.append(segment)
            if len(segment) > 4:
                for size in (2, 3, 4):
                    if len(segment) <= size:
                        continue
                    for start in range(0, len(segment) - size + 1, max(size - 1, 1)):
                        terms.append(segment[start : start + size])

        deduped: list[str] = []
        seen: set[str] = set()
        for item in terms:
            candidate = item.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
            if len(deduped) >= 12:
                break
        return deduped

    def _keyword_match_score(self, record: KnowledgeFile, query: str, terms: list[str]) -> float:
        filename = (record.filename or "").lower()
        description = (record.description or "").lower()
        full_text = (record.full_text or "").lower()
        metadata_text = self._build_metadata_search_text(record).lower()
        normalized_query = query.lower()
        score = 0.0

        if normalized_query and normalized_query in filename:
            score += 2.0
        if normalized_query and (normalized_query in description or normalized_query in full_text):
            score += 1.4
        if normalized_query and normalized_query in metadata_text:
            score += 1.1

        for term in terms:
            normalized_term = term.lower()
            if normalized_term in filename:
                score += 1.2
            if normalized_term in description:
                score += 0.9
            if normalized_term in full_text:
                score += 0.7
            if normalized_term in metadata_text:
                score += 0.8

        return score

    def _normalize_keyword_score(self, raw_score: float) -> float:
        if raw_score <= 0:
            return 0.0
        return round(min(raw_score / 6.0, 0.95), 6)

    def _build_keyword_snippet(self, record: KnowledgeFile, query: str, terms: list[str]) -> str:
        source_text = (record.description or record.full_text or record.filename or "").strip()
        if not source_text:
            return ""

        candidates = [query, *terms]
        matched_term = next((term for term in candidates if term and term.lower() in source_text.lower()), "")
        if not matched_term:
            return self._trim_snippet(source_text)

        source_lower = source_text.lower()
        index = source_lower.find(matched_term.lower())
        if index < 0:
            return self._trim_snippet(source_text)

        start = max(index - 48, 0)
        end = min(index + max(len(matched_term), 24) + 132, len(source_text))
        snippet = source_text[start:end].strip()
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(source_text):
            snippet = f"{snippet}..."
        return self._trim_snippet(snippet)

    def _fallback_snippet(self, record: KnowledgeFile) -> str:
        return self._trim_snippet((record.description or record.full_text or record.filename or "").strip())

    def _append_unique_snippet(self, snippets: list[str], snippet: str) -> None:
        normalized = self._trim_snippet(snippet)
        if not normalized:
            return
        if normalized in snippets:
            return
        snippets.append(normalized)

    def _trim_snippet(self, text: str, *, limit: int = 220) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}..."

    def _build_result_summary(self, record: KnowledgeFile) -> str:
        metadata = self._ensure_dict(record.metadata_json)
        doc_type = metadata.get("doc_type")
        source = metadata.get("source")
        if record.file_type == IMAGE_FILE_TYPE:
            return "图片资料，可直接复用视觉素材或图片说明。"
        if source == EDITOR_SNAPSHOT_SOURCE:
            return "编辑器保存的知识库快照，可检索历史版本，也可回到原文继续编辑。"
        if source == "plan_auto_ingest" and doc_type == "presentation":
            return "自动入库的 PPT 初稿，可参考页结构、讲解提纲和配图说明。"
        if source == "plan_auto_ingest":
            return "自动入库的教案资料，可参考课堂环节设计和表述方式。"
        return "上传文档资料，可作为后续生成内容的参考依据。"

    def _build_match_reason(
        self,
        *,
        record: KnowledgeFile,
        semantic_score: float,
        keyword_score: float,
        semantic_hits: int,
    ) -> str:
        metadata = self._ensure_dict(record.metadata_json)
        if semantic_score > 0.72 and semantic_hits >= 2:
            return "多个片段与查询语义高度接近，适合作为重点参考。"
        if semantic_score > 0.72:
            return "向量语义匹配较强，内容主题和查询比较贴近。"
        if metadata.get("source") == EDITOR_SNAPSHOT_SOURCE:
            return "这是编辑器保存到知识库的快照，适合回溯历史版本或检索关键片段。"
        if keyword_score > 0.6 and metadata.get("source") == "plan_auto_ingest":
            return "标题或正文关键词命中明显，且是自动沉淀的历史教学资料。"
        if keyword_score > 0.6:
            return "标题或正文关键词命中明显，可快速提取相关片段。"
        if record.file_type == IMAGE_FILE_TYPE:
            return "图片描述与查询存在关联，可用作示意图或素材候选。"
        return "与查询存在一定相关性，建议结合片段内容进一步判断。"

    def _build_metadata_search_text(self, record: KnowledgeFile) -> str:
        metadata = self._ensure_dict(record.metadata_json)
        tags = self.get_file_tags(record)
        values = [
            metadata.get("source"),
            metadata.get("trigger"),
            metadata.get("doc_type"),
            metadata.get("plan_title"),
            metadata.get("savepoint_label"),
            *tags,
        ]
        return " ".join(str(item).strip() for item in values if str(item).strip())

    def _normalize_tags(self, tags: Any) -> list[str]:
        if not isinstance(tags, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in tags:
            tag = str(item).strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            normalized.append(tag[:40])
            if len(normalized) >= 10:
                break
        return normalized

    @staticmethod
    def _ensure_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_delete_vectors(self, user_id: str, file_id: str) -> None:
        try:
            self.vector_store.delete_file(user_id, file_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to clean vector entries for %s: %s", file_id, exc)

    def _safe_unlink(self, file_path: Path) -> None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove file %s: %s", file_path, exc)

    def _distance_to_score(self, distance: Any) -> float:
        if distance is None:
            return 0.0
        try:
            numeric_distance = float(distance)
        except (TypeError, ValueError):
            return 0.0
        return round(1.0 / (1.0 + max(numeric_distance, 0.0)), 6)
