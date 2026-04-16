"""Conversation-summary tool definitions and registration."""

from __future__ import annotations

from typing import Any

from backend.tools import Tool
from backend.tools.registry import ToolsRegistry

from ..database import session_maker
from ..schemas import GetConversationSummaryArgs, SearchConversationSummariesArgs
from ..services.conversation_service import ConversationService
from ..services.plan_service import PlanService
from ..services.summary_service import ConversationSummaryService
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .registry_utils import register_tools_once


def _format_timestamp(value: Any) -> str | None:
    """Render datetimes in a compact ISO-like form for tool payloads."""
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _format_search_results(results: list[dict[str, Any]]) -> str:
    """Render conversation search results into a compact, LLM-friendly block."""
    lines = [f"找到 {len(results)} 条相关会话摘要：", ""]
    for index, item in enumerate(results, start=1):
        plan_title = str(item.get("plan_title") or "未命名文档")
        conversation_id = str(item.get("conversation_id") or "")
        summary = str(item.get("summary") or "无摘要").strip()
        status = str(item.get("status") or "unknown")
        score = float(item.get("relevance_score") or 0.0)
        lines.append(f"{index}. 文档：{plan_title}（会话：{conversation_id}，状态：{status}，相关度：{score:.2f}）")
        lines.append(f"   摘要：{summary}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_single_summary(item: dict[str, Any]) -> str:
    """Render one conversation summary into a readable text block."""
    lines = [
        f"会话 ID：{item.get('conversation_id') or ''}",
        f"文档：{item.get('plan_title') or '未命名文档'}",
        f"状态：{item.get('status') or 'unknown'}",
    ]
    started_at = item.get("started_at")
    ended_at = item.get("ended_at")
    if started_at:
        lines.append(f"开始时间：{started_at}")
    if ended_at:
        lines.append(f"结束时间：{ended_at}")
    lines.append("摘要：")
    lines.append(str(item.get("summary") or "无摘要").strip() or "无摘要")
    return "\n".join(lines)


async def search_conversation_summaries_tool(**kwargs: Any) -> dict[str, Any]:
    """Search archived conversation summaries for the current user."""
    query = kwargs["query"].strip()
    top_k = kwargs.get("top_k", 3)
    exclude_conversation_id = str(kwargs.get("exclude_conversation_id") or "").strip()
    user_id = resolve_user_id(kwargs.get("user_id"), DEFAULT_USER_ID)

    with session_maker() as session:
        service = ConversationSummaryService(session, user_id=user_id)
        results = service.search(query, top_k=top_k)

    if exclude_conversation_id:
        results = [item for item in results if str(item.get("conversation_id") or "") != exclude_conversation_id]

    if not results:
        return {
            "ok": True,
            "query": query,
            "results": [],
            "message": "未找到相关会话摘要。",
        }

    return {
        "ok": True,
        "query": query,
        "results": results,
        "message": _format_search_results(results),
    }


async def get_conversation_summary_tool(**kwargs: Any) -> dict[str, Any]:
    """Fetch one stored conversation summary by conversation id."""
    conversation_id = kwargs["conversation_id"].strip()
    user_id = resolve_user_id(kwargs.get("user_id"), DEFAULT_USER_ID)

    with session_maker() as session:
        conv_service = ConversationService(session, user_id=user_id)
        plan_service = PlanService(session, user_id=user_id)
        conversation = conv_service.get(conversation_id)
        if conversation is None:
            return {
                "ok": False,
                "conversation_id": conversation_id,
                "message": "未找到对应会话。",
            }

        plan = plan_service.get(conversation.plan_id)
        summary = str(conversation.summary or "").strip()
        result = {
            "conversation_id": conversation.id,
            "plan_id": conversation.plan_id,
            "plan_title": getattr(plan, "title", None) or "未命名文档",
            "summary": summary,
            "started_at": _format_timestamp(conversation.started_at),
            "ended_at": _format_timestamp(conversation.ended_at),
            "status": conversation.status,
        }

    if not summary:
        return {
            "ok": True,
            **result,
            "message": "该会话当前还没有可用摘要。",
        }

    return {
        "ok": True,
        **result,
        "message": _format_single_summary(result),
    }


CONVERSATION_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="search_conversation_summaries",
        description=(
            "搜索当前用户历史会话的摘要。"
            "当用户提到“之前聊过的方案”“其他会话里怎么改的”时优先使用。"
        ),
        args_schema=SearchConversationSummariesArgs,
        func=search_conversation_summaries_tool,
    ),
    Tool(
        name="get_conversation_summary",
        description=(
            "读取某个历史会话的已存摘要。"
            "通常先用 search_conversation_summaries 找到会话 ID，再读取详情。"
        ),
        args_schema=GetConversationSummaryArgs,
        func=get_conversation_summary_tool,
    ),
)


def register_conversation_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register conversation-summary tools into the target registry."""
    return register_tools_once(registry, CONVERSATION_TOOLS)
