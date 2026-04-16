"""Shared control-flow tools used by different editor runtimes."""

from __future__ import annotations

from typing import Any

from backend.tools import Tool
from backend.tools.registry import ToolsRegistry

from ..schemas import AskFollowUpArgs, ConfirmationRequest, FollowUpResult, RequestConfirmationArgs
from .registry_utils import register_tools_once

ASK_FOLLOW_UP_TOOL_NAME = "ask_follow_up"
REQUEST_CONFIRMATION_TOOL_NAME = "request_confirmation"
CONTROL_FLOW_TOOL_NAMES = frozenset({ASK_FOLLOW_UP_TOOL_NAME, REQUEST_CONFIRMATION_TOOL_NAME})


def ask_follow_up_tool(**kwargs: Any) -> dict[str, Any]:
    """Return a structured follow-up question for the editor to surface."""
    payload = FollowUpResult(
        question=kwargs["question"].strip(),
        options=kwargs.get("options"),
    )
    return payload.model_dump()


def request_confirmation_tool(**kwargs: Any) -> dict[str, Any]:
    """Return a confirmation request for the editor to pause and persist."""
    payload = ConfirmationRequest(
        operation_description=kwargs["operation_description"].strip(),
        proposed_changes=kwargs["proposed_changes"].strip(),
        tool_to_confirm=kwargs["tool_to_confirm"].strip(),
        tool_args=kwargs.get("tool_args") or {},
    )
    return payload.model_dump()


CONTROL_FLOW_TOOLS: tuple[Tool, ...] = (
    Tool(
        name=ASK_FOLLOW_UP_TOOL_NAME,
        description="当用户需求缺少关键条件、不能安全猜测时，向用户提出一个澄清问题并等待其回答。",
        args_schema=AskFollowUpArgs,
        func=ask_follow_up_tool,
    ),
    Tool(
        name=REQUEST_CONFIRMATION_TOOL_NAME,
        description="当操作可能删除、覆盖或显著改写内容时，先请求用户确认，再执行目标工具。",
        args_schema=RequestConfirmationArgs,
        func=request_confirmation_tool,
    ),
)


def register_control_flow_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register shared follow-up / confirmation tools into the target registry."""
    return register_tools_once(registry, CONTROL_FLOW_TOOLS)
