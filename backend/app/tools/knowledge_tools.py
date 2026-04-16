"""Knowledge-base tool definitions and registration."""

from __future__ import annotations

from typing import Any

from backend.tools import Tool
from backend.tools.registry import ToolsRegistry

from ..database import session_maker
from ..schemas import SearchKnowledgeArgs
from ..services.knowledge_service import DEFAULT_USER_ID, KnowledgeService
from ..user_context import resolve_user_id
from .registry_utils import register_tools_once


def _format_search_results(results: list[dict[str, Any]]) -> str:
    """Render search results into a compact, LLM-friendly text block."""
    lines = [f"找到 {len(results)} 条相关内容：", ""]
    for index, item in enumerate(results, start=1):
        filename = str(item.get("filename") or "未知文件")
        snippet = str(item.get("text_snippet") or "无文本片段").strip()
        score = float(item.get("relevance_score") or 0.0)
        file_type = str(item.get("file_type") or "unknown")
        summary = str(item.get("summary") or "").strip()
        match_reason = str(item.get("match_reason") or "").strip()
        lines.append(f"{index}. 文件：{filename}（类型：{file_type}，相关度：{score:.2f}）")
        if summary:
            lines.append(f"   概要：{summary}")
        if match_reason:
            lines.append(f"   命中原因：{match_reason}")
        lines.append(f"   片段：{snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_knowledge_service(session: Any, user_id: str) -> KnowledgeService:
    """Create a knowledge service while remaining compatible with older test doubles."""
    resolved_user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
    try:
        return KnowledgeService(session, user_id=resolved_user_id)
    except TypeError:
        service = KnowledgeService(session)
        setattr(service, "user_id", resolved_user_id)
        setattr(service, "default_user_id", resolved_user_id)
        return service


async def search_knowledge_tool(**kwargs: Any) -> dict[str, Any]:
    """Search the current user's knowledge base and return structured matches."""
    query = kwargs["query"].strip()
    top_k = kwargs.get("top_k", 3)
    file_type = kwargs.get("file_type")
    user_id = kwargs.get("user_id", DEFAULT_USER_ID)

    with session_maker() as session:
        service = _build_knowledge_service(session, user_id)
        results = await service.search(
            user_id,
            query,
            top_k=top_k,
            file_type=file_type,
        )

    if not results:
        return {
            "ok": True,
            "query": query,
            "results": [],
            "message": "未找到相关内容。",
        }

    return {
        "ok": True,
        "query": query,
        "results": results,
        "message": _format_search_results(results),
    }


KNOWLEDGE_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="search_knowledge",
        description=(
            "检索用户个人知识库中的文档片段或图片描述。"
            "当用户提到“参考我上传的资料”“看看之前的教案/图片”时优先使用。"
        ),
        args_schema=SearchKnowledgeArgs,
        func=search_knowledge_tool,
    ),
)


def register_knowledge_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register knowledge tools into the target registry."""
    return register_tools_once(registry, KNOWLEDGE_TOOLS)
