"""Conversation-summary generation and semantic retrieval."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.settings import settings
from ..database import session_maker
from ..models import Conversation, Operation, Plan
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """你是一个教案协作摘要助手。
请根据会话中的操作记录，总结这次会话的核心内容，要求：
1. 输出中文，不超过200字。
2. 明确指出教案标题。
3. 概括主要修改内容。
4. 提炼关键决策或最终方向。
5. 不要输出项目符号、标题或额外解释。"""


@dataclass(slots=True)
class SummaryGenerationResult:
    """Outcome of one summary generation run."""

    conversation_id: str
    summary: str
    indexed: bool


class ConversationSummaryStore:
    """Chroma-backed store for conversation summaries."""

    def __init__(self, persist_directory: str, collection_name: str) -> None:
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._client: Any | None = None
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("缺少 chromadb 依赖，无法执行会话语义检索。") from exc

            self._client = chromadb.PersistentClient(path=self.persist_directory)
        return self._client

    def _get_collection(self):
        return self._get_client().get_or_create_collection(name=self.collection_name)

    def upsert(
        self,
        *,
        conversation_id: str,
        summary: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Write or update one indexed summary."""
        self._get_collection().upsert(
            ids=[conversation_id],
            documents=[summary],
            embeddings=[embedding],
            metadatas=[metadata],
        )

    def query(self, *, user_id: str, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        """Query semantically similar conversation summaries."""
        try:
            raw_result = self._get_collection().query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
                where={"user_id": user_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation summary semantic query failed: %s", exc)
            return []

        ids = (raw_result.get("ids") or [[]])[0]
        documents = (raw_result.get("documents") or [[]])[0]
        metadatas = (raw_result.get("metadatas") or [[]])[0]
        distances = (raw_result.get("distances") or [[]])[0]

        results: list[dict[str, Any]] = []
        for index, conversation_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            results.append(
                {
                    "conversation_id": conversation_id,
                    "summary": documents[index] if index < len(documents) else "",
                    "metadata": metadata or {},
                    "distance": distances[index] if index < len(distances) else None,
                }
            )
        return results


class ConversationSummaryService:
    """Generate summaries and search archived conversations."""

    def __init__(
        self,
        db: Session,
        user_id: str = "default",
        *,
        embedding_service: EmbeddingService | Any | None = None,
        llm_client: Any | None = None,
        vector_store: ConversationSummaryStore | None = None,
    ) -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
        self._embedding_service = embedding_service
        self._llm_client = llm_client
        self._vector_store = vector_store

    @property
    def embedding_service(self) -> EmbeddingService | Any:
        if self._embedding_service is None:
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def llm_client(self) -> Any:
        if self._llm_client is None:
            from openai import OpenAI

            self._llm_client = OpenAI(
                api_key=settings.SUMMARY_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        return self._llm_client

    @property
    def vector_store(self) -> ConversationSummaryStore:
        if self._vector_store is None:
            self._vector_store = ConversationSummaryStore(
                persist_directory=settings.CHROMA_PERSIST_DIR,
                collection_name=settings.CONVERSATION_SUMMARY_COLLECTION,
            )
        return self._vector_store

    def generate_summary(self, conv_id: str) -> SummaryGenerationResult:
        """Generate, persist, and index a conversation summary."""
        conversation = self.db.execute(
            select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == self.user_id)
        ).scalar_one_or_none()
        if conversation is None:
            raise ValueError("Conversation not found.")

        plan = self.db.execute(
            select(Plan).where(Plan.id == conversation.plan_id, Plan.user_id == self.user_id)
        ).scalar_one_or_none()
        if plan is None:
            raise ValueError("Plan not found.")

        operations = self._list_operations(conv_id, limit=50)
        summary = self._build_summary_text(plan=plan, operations=operations)
        if not summary:
            return SummaryGenerationResult(conversation_id=conv_id, summary="", indexed=False)

        self._persist_summary(conversation, summary)
        indexed = self._try_index_summary(conversation=conversation, plan=plan, summary=summary)
        return SummaryGenerationResult(conversation_id=conv_id, summary=summary, indexed=indexed)

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """Search archived conversations with semantic retrieval and SQL fallback."""
        normalized_query = query.strip()
        if not normalized_query:
            return []

        semantic_results = self._semantic_search(normalized_query, top_k=top_k)
        if semantic_results:
            return semantic_results

        return self._keyword_fallback_search(normalized_query, top_k=top_k)

    def _semantic_search(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        try:
            query_embedding = self.embedding_service.embed([query])[0]
            matches = self._query_vector_store(query_embedding=query_embedding, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation semantic retrieval unavailable, fallback to SQL search: %s", exc)
            return []

        if not matches:
            return []

        conversation_ids = [item["conversation_id"] for item in matches if item.get("conversation_id")]
        if not conversation_ids:
            return []

        conversations = self.db.execute(
            select(Conversation).where(Conversation.id.in_(conversation_ids), Conversation.user_id == self.user_id)
        ).scalars().all()
        conversation_map = {item.id: item for item in conversations}

        results: list[dict[str, Any]] = []
        for item in matches:
            conversation_id = item.get("conversation_id")
            conversation = conversation_map.get(conversation_id)
            if conversation is None:
                continue

            metadata = item.get("metadata") or {}
            summary = (metadata.get("summary") or item.get("summary") or conversation.summary or "").strip()
            if not summary:
                continue

            results.append(
                {
                    "conversation_id": conversation.id,
                    "plan_id": conversation.plan_id,
                    "plan_title": metadata.get("plan_title") or self._get_plan_title(conversation.plan_id),
                    "summary": summary,
                    "relevance_score": self._distance_to_score(item.get("distance")),
                    "started_at": conversation.started_at,
                    "ended_at": conversation.ended_at,
                    "status": conversation.status,
                }
            )
        return results

    def _query_vector_store(self, *, query_embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        try:
            return self.vector_store.query(user_id=self.user_id, query_embedding=query_embedding, top_k=top_k)
        except TypeError:
            return self.vector_store.query(query_embedding=query_embedding, top_k=top_k)

    def _keyword_fallback_search(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        keyword = f"%{query}%"
        conversations = self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == self.user_id)
            .where(Conversation.summary.is_not(None))
            .where(Conversation.summary.like(keyword))
            .order_by(Conversation.started_at.desc())
            .limit(top_k)
        ).scalars().all()

        results: list[dict[str, Any]] = []
        for conversation in conversations:
            summary = (conversation.summary or "").strip()
            if not summary:
                continue

            results.append(
                {
                    "conversation_id": conversation.id,
                    "plan_id": conversation.plan_id,
                    "plan_title": self._get_plan_title(conversation.plan_id),
                    "summary": summary,
                    "relevance_score": 0.0,
                    "started_at": conversation.started_at,
                    "ended_at": conversation.ended_at,
                    "status": conversation.status,
                }
            )
        return results

    def _build_summary_text(self, *, plan: Plan, operations: list[Operation]) -> str:
        llm_summary = self._generate_summary_via_llm(plan=plan, operations=operations)
        if llm_summary:
            return llm_summary
        return self._build_fallback_summary(plan=plan, operations=operations)

    def _generate_summary_via_llm(self, *, plan: Plan, operations: list[Operation]) -> str:
        if not settings.SUMMARY_API_KEY:
            logger.warning("SUMMARY_API_KEY 未配置，跳过 LLM 摘要生成。")
            return ""

        prompt = self._build_prompt(plan=plan, operations=operations)
        try:
            response = self.llm_client.chat.completions.create(
                model=settings.SUMMARY_MODEL_NAME,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation summary LLM generation failed for %s: %s", plan.id, exc)
            return ""

        content = response.choices[0].message.content if response.choices else ""
        return (content or "").strip()[:200]

    def _build_prompt(self, *, plan: Plan, operations: list[Operation]) -> str:
        payload = [
            {
                "tool": item.tool_name,
                "arguments": self._truncate_payload(item.arguments),
                "result": self._truncate_payload(item.result),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in operations
        ]
        return (
            f"教案标题：{plan.title}\n"
            f"教案类型：{plan.doc_type}\n"
            f"学科：{plan.subject or '未提供'}\n"
            f"年级：{plan.grade or '未提供'}\n"
            f"操作记录：\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "请输出最终摘要。"
        )

    def _build_fallback_summary(self, *, plan: Plan, operations: list[Operation]) -> str:
        if not operations:
            return f"围绕《{plan.title}》建立了一次会话，暂未记录到具体修改操作。"

        tool_names = [item.tool_name for item in operations if item.tool_name]
        unique_tools: list[str] = []
        for tool_name in tool_names:
            if tool_name not in unique_tools:
                unique_tools.append(tool_name)

        focus_text = "、".join(unique_tools[:3])
        if len(unique_tools) > 3:
            focus_text = f"{focus_text}等操作"
        else:
            focus_text = f"{focus_text}等操作" if focus_text else "多项操作"

        latest_operation = operations[-1]
        return (
            f"本次会话围绕《{plan.title}》进行了{len(operations)}次调整，"
            f"主要涉及{focus_text}，最终集中在与“{latest_operation.tool_name}”相关的内容优化和决策确认。"
        )[:200]

    def _persist_summary(self, conversation: Conversation, summary: str) -> None:
        conversation.summary = summary
        try:
            self.db.commit()
            self.db.refresh(conversation)
        except Exception:
            self.db.rollback()
            raise

    def _try_index_summary(self, *, conversation: Conversation, plan: Plan, summary: str) -> bool:
        try:
            embedding = self.embedding_service.embed([summary])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation summary embedding failed for %s: %s", conversation.id, exc)
            return False

        try:
            conversation.summary_embedding = embedding
            self.db.commit()
            self.db.refresh(conversation)
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            logger.warning("Failed to persist summary embedding for %s: %s", conversation.id, exc)
            return False

        metadata = {
            "conv_id": conversation.id,
            "plan_id": plan.id,
            "plan_title": plan.title,
            "summary": summary,
            "user_id": self.user_id,
        }
        try:
            self.vector_store.upsert(
                conversation_id=conversation.id,
                summary=summary,
                embedding=embedding,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation summary vector indexing failed for %s: %s", conversation.id, exc)
            return False

        return True

    def _get_plan_title(self, plan_id: str) -> str:
        plan = self.db.execute(
            select(Plan).where(Plan.id == plan_id, Plan.user_id == self.user_id)
        ).scalar_one_or_none()
        return plan.title if plan is not None else ""

    def _list_operations(self, conv_id: str, *, limit: int) -> list[Operation]:
        return list(
            self.db.execute(
                select(Operation)
                .where(Operation.conversation_id == conv_id, Operation.user_id == self.user_id)
                .order_by(Operation.created_at.asc())
                .limit(limit)
            ).scalars().all()
        )

    def _truncate_payload(self, value: Any, *, limit: int = 400) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return value[:limit]
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except TypeError:
            serialized = str(value)
        if len(serialized) <= limit:
            return value
        return f"{serialized[:limit]}..."

    def _distance_to_score(self, distance: Any) -> float:
        if distance is None:
            return 0.0
        try:
            numeric_distance = float(distance)
        except (TypeError, ValueError):
            return 0.0
        return round(1.0 / (1.0 + max(numeric_distance, 0.0)), 6)


def generate_conversation_summary(conv_id: str, db: Session, user_id: str) -> SummaryGenerationResult:
    """Helper for generating one conversation summary with the current session."""
    service = ConversationSummaryService(db, user_id=user_id)
    return service.generate_summary(conv_id)


def generate_conversation_summary_task(conv_id: str, user_id: str) -> None:
    """Background task entrypoint for summary generation."""
    with session_maker() as session:
        try:
            ConversationSummaryService(session, user_id=user_id).generate_summary(conv_id)
        except Exception:  # noqa: BLE001
            logger.exception("Background conversation summary generation failed for %s.", conv_id)


__all__ = [
    "ConversationSummaryService",
    "SummaryGenerationResult",
    "generate_conversation_summary",
    "generate_conversation_summary_task",
]
