"""Shared guardrail helpers for editor planning and task validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..schemas import Task
from ...tools import ToolsRegistry

FULL_CONTENT_REQUEST_KEYWORDS = (
    "完整",
    "全文",
    "完整示例",
    "完整流程",
    "完整讲解",
    "最终正文",
    "成稿",
    "full",
)
RUNNABLE_CODE_REQUEST_KEYWORDS = (
    "完整代码",
    "可运行",
    "直接运行",
    "runnable",
    "end-to-end",
)
CODE_REQUEST_HINTS = (
    "代码",
    "code",
    "agent",
    "脚本",
    "函数",
    "类",
    "api",
    "python",
    "javascript",
    "typescript",
    "java",
)
CONTENT_QUALITY_BLOCK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"简化(?:版|示例|实现)?"), "包含“简化示例/简化实现”一类表述"),
    (re.compile(r"伪代码|示意(?:稿|版)?|占位"), "包含伪实现或示意稿表述"),
    (re.compile(r"(?:根据|按)实际(?:情况|需求|业务).{0,8}(?:调整|修改|替换)"), "要求后续再按实际情况调整"),
    (re.compile(r"可根据(?:实际|需要|情况)"), "保留了“可根据需要再改”的占位说法"),
    (re.compile(r"仅供参考|自行(?:补充|实现)|此处.*(?:省略|略去)|TODO"), "内容仍像待补全草稿"),
)
CODE_STRUCTURE_HINTS = ("def ", "class ", "import ", "from ", "return ", "async ", "await ", "function ", "const ", "let ", "```")


class EditorGuardrails:
    """Encapsulate shared validation and content-quality guardrails."""

    def __init__(
        self,
        tools_registry: ToolsRegistry,
        *,
        json_ready: Callable[[Any], Any],
        clean_text: Callable[[Any], str],
    ) -> None:
        self.tools_registry = tools_registry
        self.json_ready = json_ready
        self.clean_text = clean_text

    def validate_tool_arguments(
        self,
        tool_name: str | None,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Validate normalized arguments against the selected tool schema."""
        if not tool_name:
            return arguments, ["缺少可执行的 tool_name"]

        try:
            tool = self.tools_registry.get_tool(tool_name)
        except Exception as exc:  # noqa: BLE001
            return arguments, [str(exc)]

        try:
            validated = tool.args_schema.model_validate(arguments)
        except ValidationError as exc:
            return arguments, self.format_tool_validation_issues(exc)
        except Exception as exc:  # noqa: BLE001
            return arguments, [str(exc)]
        return self.json_ready(validated.model_dump()), None

    def format_tool_validation_issues(self, exc: ValidationError) -> list[str]:
        """Convert pydantic validation errors into short readable issues."""
        issues: list[str] = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()))
            message = str(error.get("msg") or "参数不符合要求")
            location = self.humanize_validation_location(location)
            message = self.humanize_validation_message(message)
            if location:
                issues.append(f"{location}: {message}")
            else:
                issues.append(message)
        return issues or ["参数不符合工具要求"]

    def humanize_validation_location(self, location: str) -> str:
        """Translate raw validation paths into user-facing labels."""
        if not location:
            return location

        parts = location.split(".")
        if len(parts) >= 3 and parts[0] == "slides" and parts[1].isdigit():
            field_map = {
                "title": "标题",
                "subtitle": "副标题",
                "body": "正文",
                "template": "模板",
                "layout": "版式",
                "image_description": "图片说明",
                "image_url": "图片地址",
                "notes": "备注",
                "source_section": "来源环节",
            }
            slide_number = int(parts[1]) + 1
            field_label = field_map.get(parts[2], parts[2])
            return f"第 {slide_number} 页的{field_label}"
        return location

    def humanize_validation_message(self, message: str) -> str:
        """Rewrite generic validator output into more actionable Chinese hints."""
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :].strip()
        mapping = {
            "Input should be a valid string": "需要填写文本；如果不需要内容，请传空字符串",
            "Input should be a valid dictionary": "需要提供对象格式",
            "Field required": "这是必填项",
        }
        return mapping.get(message, message)

    def humanize_validation_issue_text(self, issue: str) -> str:
        """Humanize preformatted validation issue strings as a final fallback."""
        raw = str(issue or "").strip()
        if not raw:
            return raw
        if ": " in raw:
            location, message = raw.split(": ", 1)
            return f"{self.humanize_validation_location(location)}: {self.humanize_validation_message(message)}"
        return self.humanize_validation_message(raw)

    def describe_invalid_task_action(
        self,
        task: Task,
        *,
        resolve_tool_name: Callable[[Task], str | None],
    ) -> str:
        """Choose a human-friendly action label for follow-up questions."""
        tool_name = resolve_tool_name(task)
        if tool_name == "replace_presentation":
            return "整体重排这份 PPT"
        if tool_name == "update_slide_content":
            return "修改这页 PPT"
        if tool_name == "change_layout":
            return "调整这一页版式"
        if tool_name == "add_slide":
            return "新增这一页 PPT"
        if tool_name == "delete_slide":
            return "删除这一页 PPT"
        if tool_name == "add_notes":
            return "补充这一页备注"
        action = self.clean_text(task.action)
        return action or "执行这一步"

    def build_invalid_task_follow_up(
        self,
        task: Task,
        issues: list[str],
        *,
        resolve_tool_name: Callable[[Task], str | None],
    ) -> dict[str, Any]:
        """Ask the user for missing/invalid tool inputs instead of guessing."""
        action = self.describe_invalid_task_action(task, resolve_tool_name=resolve_tool_name)
        issue_text = "；".join(
            self.humanize_validation_issue_text(issue)
            for issue in issues
            if str(issue or "").strip()
        )
        question = f"我还不能可靠{action}，缺少或不符合要求的信息有：{issue_text}。请补充后我继续处理。"
        return {
            "type": "follow_up",
            "question": question,
            "options": None,
        }

    def build_content_quality_context(
        self,
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Infer whether the request expects final-form content such as runnable code."""
        parts = [str(user_message or "").strip()]
        if pending_follow_up:
            parts.append(str(pending_follow_up.get("root_user_message") or "").strip())
            parts.append(str(pending_follow_up.get("previous_user_message") or "").strip())
            parts.append(str(pending_follow_up.get("question") or "").strip())
        request_text = " ".join(part for part in parts if part).lower()
        requires_complete_content = any(keyword in request_text for keyword in FULL_CONTENT_REQUEST_KEYWORDS)
        requires_runnable_code = any(keyword in request_text for keyword in RUNNABLE_CODE_REQUEST_KEYWORDS) or (
            requires_complete_content and any(keyword in request_text for keyword in CODE_REQUEST_HINTS)
        )
        return {
            "request_text": request_text,
            "requires_complete_content": requires_complete_content,
            "requires_runnable_code": requires_runnable_code,
        }

    def validate_task_content_quality(
        self,
        task: Task,
        quality_context: dict[str, Any] | None,
        *,
        build_task_arguments: Callable[[Task], dict[str, Any]],
    ) -> list[str] | None:
        """Block placeholder-like draft text when the user asked for final content."""
        if task.type != "modify" or not quality_context:
            return None
        if not quality_context.get("requires_complete_content"):
            return None

        arguments = build_task_arguments(task)
        candidate = ""
        for field_name in ("new_content", "content"):
            value = arguments.get(field_name)
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
                break
        if not candidate:
            return None

        issues: list[str] = []
        for pattern, reason in CONTENT_QUALITY_BLOCK_PATTERNS:
            if pattern.search(candidate):
                issues.append(reason)
        if quality_context.get("requires_runnable_code"):
            lowered = candidate.lower()
            if len(candidate) < 120 or not any(hint in lowered for hint in CODE_STRUCTURE_HINTS):
                issues.append("内容长度或结构仍不像可直接运行的代码正文")
        return issues or None

    def build_content_quality_follow_up(
        self,
        task: Task,
        issues: list[str],
        *,
        build_task_arguments: Callable[[Task], dict[str, Any]],
        infer_task_subject: Callable[[dict[str, Any], str], str],
    ) -> dict[str, Any]:
        """Ask for tighter constraints when the generated content still looks like a draft."""
        action = self.clean_text(task.action) or "写入这段内容"
        arguments = build_task_arguments(task)
        subject = infer_task_subject(arguments, self.clean_text(task.target))
        issue_text = "；".join(issue for issue in issues if issue)
        question = (
            f"我还不能可靠{action}“{subject}”，因为当前生成内容仍像示意稿：{issue_text}。"
            "请补充必须保留的实现约束或成稿要求，我再继续生成可直接落入教案的最终内容。"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": None,
        }
