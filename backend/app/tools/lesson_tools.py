"""Lesson-plan specific tool definitions and registration."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, model_validator

from backend.tools import Tool
from backend.tools.cancellation import CancellationToken
from backend.tools.registry import ToolsRegistry

from ..database import session_maker
from ..schemas import (
    AddImagePlaceholderArgs,
    OperationCreate,
    PlanUpdate,
)
from ..services.lesson_validator import validate_plan
from ..services.operation_service import OperationService
from ..services.plan_service import PlanService
from .control_flow_tools import CONTROL_FLOW_TOOLS
from .registry_utils import register_tools_once


class RewriteSectionArgs(BaseModel):
    """Arguments for rewriting a section."""

    section_type: str = Field(..., min_length=1, description="要重写的章节类型，例如“导入”“新授”。")
    new_content: str = Field(..., min_length=1, description="章节的新内容。")
    preserve_duration: bool = Field(default=True, description="是否保持原有时长。")


class AdjustDurationArgs(BaseModel):
    """Arguments for adjusting a section duration."""

    section_type: str = Field(..., min_length=1, description="要调整时长的章节类型。")
    new_duration: int = Field(..., ge=0, description="调整后的章节时长，单位分钟。")


class InsertElementArgs(BaseModel):
    """Arguments for inserting an element into a section."""

    target_section: str = Field(..., min_length=1, description="目标章节类型。")
    position: Literal["start", "end", "after_paragraph"] = Field(
        ...,
        description="插入位置：章节开头、结尾或首段之后。",
    )
    element_type: str = Field(..., min_length=1, description="元素类型，例如提问、案例、板书。")
    content: str = Field(..., min_length=1, description="插入元素的内容。")


class InsertSectionArgs(BaseModel):
    """Arguments for inserting a new section."""

    section_type: str = Field(..., min_length=1, description="新章节类型，例如“练习”“拓展”。")
    content: str = Field(..., min_length=1, description="新章节内容。")
    duration: int = Field(default=0, ge=0, description="新章节时长，单位分钟。")
    position: Literal["start", "end", "before", "after"] = Field(
        default="end",
        description="插入位置：开头、结尾、某章节前或某章节后。",
    )
    reference_section: str | None = Field(default=None, description="当 position 为 before/after 时，用于定位的章节类型。")
    reference_index: int | None = Field(default=None, ge=0, description="当 position 为 before/after 时，也可直接提供章节索引。")
    elements: list[dict[str, Any]] = Field(default_factory=list, description="可选的结构化教学元素列表。")

    @model_validator(mode="after")
    def validate_reference(self) -> "InsertSectionArgs":
        """Require a reference section when inserting relative to another section."""
        if self.position in {"before", "after"} and self.reference_section is None and self.reference_index is None:
            raise ValueError("position 为 before/after 时必须提供 reference_section 或 reference_index。")
        return self


class MoveSectionArgs(BaseModel):
    """Arguments for reordering a section."""

    section_type: str | None = Field(default=None, description="要移动的章节类型。")
    section_index: int | None = Field(default=None, ge=0, description="要移动的章节索引，从 0 开始。")
    new_index: int = Field(..., ge=0, description="移动后的目标索引，从 0 开始。")

    @model_validator(mode="after")
    def validate_source(self) -> "MoveSectionArgs":
        """Require either section_type or section_index."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        return self


class DeleteSectionArgs(BaseModel):
    """Arguments for deleting a section."""

    section_type: str | None = Field(default=None, description="要删除的章节类型。")
    section_index: int | None = Field(default=None, ge=0, description="要删除的章节索引，从 0 开始。")

    @model_validator(mode="after")
    def validate_identifier(self) -> "DeleteSectionArgs":
        """Require either section_type or section_index."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        return self


class SearchInPlanArgs(BaseModel):
    """Arguments for searching within a plan."""

    keyword: str = Field(..., min_length=1, description="要搜索的关键词。")


class GetSectionDetailsArgs(BaseModel):
    """Arguments for fetching one section with rich editing context."""

    section_type: str | None = Field(default=None, description="目标章节类型，例如“新授”“练习”。")
    section_index: int | None = Field(default=None, ge=0, description="目标章节索引，从 0 开始。")
    include_neighbors: bool = Field(default=True, description="是否附带前后相邻章节的摘要。")

    @model_validator(mode="after")
    def validate_locator(self) -> "GetSectionDetailsArgs":
        """Require either section_type or section_index."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        return self


class GetTextContextInPlanArgs(BaseModel):
    """Arguments for locating one exact text snippet with richer context."""

    target_text: str = Field(..., min_length=1, description="要定位的原文片段。")
    section_type: str | None = Field(default=None, description="可选：只在某个章节内定位。")
    max_matches: int = Field(default=5, ge=1, le=10, description="最多返回多少处匹配。")


class ReplaceTextInPlanArgs(BaseModel):
    """Arguments for replacing text in the plan."""

    target_text: str = Field(..., min_length=1, description="要替换的原文。")
    replacement_text: str = Field(..., description="替换成的新文本，可为空字符串。")
    section_type: str | None = Field(default=None, description="可选：只在某个章节内替换。")
    replace_all: bool = Field(default=True, description="是否替换所有命中；否则只替换第一处。")


class ReplaceParagraphsInSectionArgs(BaseModel):
    """Arguments for replacing one or more paragraphs inside a section."""

    section_type: str | None = Field(default=None, description="目标章节类型。")
    section_index: int | None = Field(default=None, ge=0, description="目标章节索引，从 0 开始。")
    start_paragraph_index: int = Field(..., ge=0, description="起始段落索引，从 0 开始。")
    end_paragraph_index: int | None = Field(default=None, ge=0, description="结束段落索引，默认与起始段落相同。")
    new_text: str = Field(..., min_length=1, description="替换后的新段落文本。")

    @model_validator(mode="after")
    def validate_locator(self) -> "ReplaceParagraphsInSectionArgs":
        """Require a section locator and a valid paragraph range."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        if self.end_paragraph_index is not None and self.end_paragraph_index < self.start_paragraph_index:
            raise ValueError("end_paragraph_index 不能小于 start_paragraph_index。")
        return self


class InsertParagraphsInSectionArgs(BaseModel):
    """Arguments for inserting one paragraph block into a section."""

    section_type: str | None = Field(default=None, description="目标章节类型。")
    section_index: int | None = Field(default=None, ge=0, description="目标章节索引，从 0 开始。")
    position: Literal["start", "end", "before", "after"] = Field(
        ...,
        description="插入位置：章节开头、结尾、某段之前或某段之后。",
    )
    paragraph_index: int | None = Field(default=None, ge=0, description="当 position 为 before/after 时，用于定位的段落索引。")
    new_text: str = Field(..., min_length=1, description="要插入的新段落文本。")

    @model_validator(mode="after")
    def validate_locator(self) -> "InsertParagraphsInSectionArgs":
        """Require a section locator and paragraph anchor when needed."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        if self.position in {"before", "after"} and self.paragraph_index is None:
            raise ValueError("position 为 before/after 时必须提供 paragraph_index。")
        return self


class DeleteParagraphsInSectionArgs(BaseModel):
    """Arguments for deleting one or more paragraphs inside a section."""

    section_type: str | None = Field(default=None, description="目标章节类型。")
    section_index: int | None = Field(default=None, ge=0, description="目标章节索引，从 0 开始。")
    start_paragraph_index: int = Field(..., ge=0, description="起始段落索引，从 0 开始。")
    end_paragraph_index: int | None = Field(default=None, ge=0, description="结束段落索引，默认与起始段落相同。")

    @model_validator(mode="after")
    def validate_locator(self) -> "DeleteParagraphsInSectionArgs":
        """Require a section locator and a valid paragraph range."""
        if self.section_type is None and self.section_index is None:
            raise ValueError("section_type 和 section_index 至少提供一个。")
        if self.end_paragraph_index is not None and self.end_paragraph_index < self.start_paragraph_index:
            raise ValueError("end_paragraph_index 不能小于 start_paragraph_index。")
        return self


class EvaluatePlanSuitabilityArgs(BaseModel):
    """Arguments for evaluating overall plan suitability."""

    focus: str | None = Field(default=None, description="评估重点，例如“难度”“是否适合作为第一课入门”。")


IMAGE_PLACEHOLDER_TARGET = "upload_needed"


@contextmanager
def _tool_services(user_id: str) -> Iterator[tuple[PlanService, OperationService]]:
    """Build fresh synchronous services for tool execution."""
    with session_maker() as session:
        yield PlanService(session, user_id=user_id), OperationService(session, user_id=user_id)


def _section_matches(section: dict[str, Any], section_type: str) -> bool:
    """Match a target section by common name fields."""
    target = section_type.strip().lower()
    for key in ("type", "section_type", "title", "name"):
        value = section.get(key)
        if isinstance(value, str) and value.strip().lower() == target:
            return True
    return False


def _find_section_index(sections: list[dict[str, Any]], section_type: str) -> int | None:
    """Locate a section by type-like fields."""
    for index, section in enumerate(sections):
        if isinstance(section, dict) and _section_matches(section, section_type):
            return index
    return None


def _resolve_section_index(
    sections: list[dict[str, Any]],
    section_type: str | None = None,
    section_index: int | None = None,
) -> int | None:
    """Locate a section by type or explicit index."""
    if section_index is not None:
        if 0 <= section_index < len(sections):
            return section_index
        return None
    if section_type is None:
        return None
    return _find_section_index(sections, section_type)


def _section_label(section: dict[str, Any], fallback: str) -> str:
    """Return the most readable section label."""
    return str(
        section.get("type")
        or section.get("section_type")
        or section.get("title")
        or section.get("name")
        or fallback
    )


def _ensure_plan_content(content: Any) -> dict[str, Any]:
    """Normalize lesson-plan content into a mutable dict."""
    if not isinstance(content, dict):
        return {"sections": []}
    normalized = deepcopy(content)
    sections = normalized.get("sections")
    if not isinstance(sections, list):
        normalized["sections"] = []
    return normalized


def _render_element_text(element_type: str, content: str) -> str:
    """Render inserted element text into a simple readable block."""
    return f"[{element_type}] {content}".strip()


def _build_image_placeholder(description: str) -> str:
    """Create the canonical Markdown image placeholder."""
    return f"![图片：{description}]({IMAGE_PLACEHOLDER_TARGET})"


def _split_content_blocks(raw_content: Any) -> tuple[list[str], str]:
    """Split textual content while preserving its existing paragraph style."""
    if not isinstance(raw_content, str) or not raw_content.strip():
        return [], "\n"

    separator = "\n\n" if "\n\n" in raw_content else "\n"
    paragraphs = [item for item in raw_content.split(separator) if item.strip()]
    return paragraphs, separator


def _update_textual_content(section: dict[str, Any], position: str, element_text: str) -> None:
    """Mirror an inserted element into string content for export/search."""
    raw_content = section.get("content")
    if not isinstance(raw_content, str):
        section["content"] = element_text
        return

    paragraphs = [item for item in raw_content.split("\n\n") if item.strip()]
    if position == "start":
        paragraphs.insert(0, element_text)
    elif position == "after_paragraph" and paragraphs:
        paragraphs.insert(1, element_text)
    else:
        paragraphs.append(element_text)
    section["content"] = "\n\n".join(paragraphs)


def _truncate_text(text: str, limit: int = 120) -> str:
    """Trim a long text fragment for compact operation logging."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}..."


def _compact_element_payload(payload: Any) -> dict[str, Any]:
    """Summarize one teaching element for operation logs."""
    if not isinstance(payload, dict):
        return {"summary": _truncate_text(str(payload), 80)}
    compact = {
        "type": payload.get("type"),
        "position": payload.get("position"),
        "status": payload.get("status"),
    }
    for field, target_key in (
        ("content", "content_preview"),
        ("description", "description_preview"),
        ("placeholder", "placeholder_preview"),
    ):
        value = str(payload.get(field) or "").strip()
        if value:
            compact[target_key] = _truncate_text(value.replace("\n", " "), 100)
    if payload.get("paragraph_index") is not None:
        compact["paragraph_index"] = payload.get("paragraph_index")
    return {key: value for key, value in compact.items() if value is not None and value != ""}


def _compact_neighbor_payload(payload: Any) -> dict[str, Any] | None:
    """Summarize one adjacent section payload."""
    if not isinstance(payload, dict):
        return None
    compact = {
        "section_index": payload.get("section_index"),
        "section_type": payload.get("section_type"),
        "duration": payload.get("duration"),
    }
    preview = str(payload.get("content_preview") or "").strip()
    if preview:
        compact["content_preview"] = _truncate_text(preview.replace("\n", " "), 120)
    return {key: value for key, value in compact.items() if value is not None and value != ""}


def _compact_section_payload(payload: Any) -> dict[str, Any]:
    """Turn a rich section payload into a compact section summary."""
    if not isinstance(payload, dict):
        return {"summary": _truncate_text(str(payload), 120)}

    compact = {
        "section_index": payload.get("section_index"),
        "section_type": payload.get("section_type"),
        "duration": payload.get("duration"),
    }
    content = str(payload.get("content") or "").strip()
    if content:
        compact["content_preview"] = _truncate_text(content.replace("\n", " "), 160)

    paragraphs = payload.get("paragraphs", [])
    if isinstance(paragraphs, list):
        compact["paragraphs_count"] = len(paragraphs)
        compact["paragraphs_preview"] = [
            {
                "index": item.get("index"),
                "text": _truncate_text(str(item.get("text") or "").replace("\n", " "), 100),
            }
            for item in paragraphs[:3]
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]

    elements = payload.get("elements", [])
    if isinstance(elements, list):
        compact["elements_count"] = len(elements)
        compact["elements_preview"] = [_compact_element_payload(item) for item in elements[:3]]

    previous_section = _compact_neighbor_payload(payload.get("previous_section"))
    if previous_section is not None:
        compact["previous_section"] = previous_section
    next_section = _compact_neighbor_payload(payload.get("next_section"))
    if next_section is not None:
        compact["next_section"] = next_section

    return {
        key: value
        for key, value in compact.items()
        if value is not None and value != "" and value != []
    }


def _compact_match_payload(payload: Any) -> dict[str, Any]:
    """Keep match payloads compact while preserving location and snippets."""
    if not isinstance(payload, dict):
        return {"summary": _truncate_text(str(payload), 100)}

    compact = {}
    for key in ("section", "section_index", "section_type", "path"):
        if key in payload:
            compact[key] = payload.get(key)

    snippet = str(payload.get("snippet") or "").strip()
    if snippet:
        compact["snippet"] = _truncate_text(snippet.replace("\n", " "), 120)

    paragraph_matches = payload.get("paragraph_matches", [])
    if isinstance(paragraph_matches, list) and paragraph_matches:
        compact["paragraph_matches_count"] = len(paragraph_matches)
        compact["paragraph_matches_preview"] = [
            {
                "index": item.get("index"),
                "text": _truncate_text(str(item.get("text") or "").replace("\n", " "), 100),
            }
            for item in paragraph_matches[:3]
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]

    return {key: value for key, value in compact.items() if value is not None and value != "" and value != []}


def _compact_operation_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep only the most useful and compact operation arguments for persistence."""
    compact: dict[str, Any] = {}
    if "plan_id" in arguments:
        compact["plan_id"] = arguments["plan_id"]

    if tool_name in {"rewrite_section", "adjust_duration"}:
        compact["section_type"] = arguments.get("section_type")
        if tool_name == "rewrite_section":
            new_content = str(arguments.get("new_content") or "").strip()
            if new_content:
                compact["new_content_preview"] = _truncate_text(new_content.replace("\n", " "), 140)
            if arguments.get("preserve_duration") is not None:
                compact["preserve_duration"] = bool(arguments.get("preserve_duration"))
        else:
            compact["new_duration"] = arguments.get("new_duration")
        return compact

    if tool_name == "insert_element":
        compact.update(
            {
                "target_section": arguments.get("target_section"),
                "position": arguments.get("position"),
                "element_type": arguments.get("element_type"),
            }
        )
        content = str(arguments.get("content") or "").strip()
        if content:
            compact["content_preview"] = _truncate_text(content.replace("\n", " "), 120)
        return compact

    if tool_name == "insert_section":
        compact.update(
            {
                "section_type": arguments.get("section_type"),
                "duration": arguments.get("duration", 0),
                "position": arguments.get("position", "end"),
                "reference_section": arguments.get("reference_section"),
                "reference_index": arguments.get("reference_index"),
            }
        )
        content = str(arguments.get("content") or "").strip()
        if content:
            compact["content_preview"] = _truncate_text(content.replace("\n", " "), 140)
        elements = arguments.get("elements", [])
        if isinstance(elements, list) and elements:
            compact["elements_count"] = len(elements)
            compact["elements_preview"] = [_compact_element_payload(item) for item in elements[:3]]
        return {key: value for key, value in compact.items() if value is not None and value != "" and value != []}

    if tool_name in {"delete_section", "move_section"}:
        if arguments.get("section_type") is not None:
            compact["section_type"] = arguments.get("section_type")
        if arguments.get("section_index") is not None:
            compact["section_index"] = arguments.get("section_index")
        if tool_name == "move_section":
            compact["new_index"] = arguments.get("new_index")
        return compact

    if tool_name == "add_image_placeholder":
        compact.update(
            {
                "section_type": arguments.get("section_type"),
                "position": arguments.get("position"),
            }
        )
        if arguments.get("paragraph_index") is not None:
            compact["paragraph_index"] = arguments.get("paragraph_index")
        description = str(arguments.get("description") or "").strip()
        if description:
            compact["description"] = _truncate_text(description, 100)
        return compact

    if tool_name in {"search_in_plan", "get_text_context_in_plan"}:
        if tool_name == "search_in_plan":
            compact["keyword"] = str(arguments.get("keyword") or "").strip()
        else:
            compact["target_text"] = _truncate_text(str(arguments.get("target_text") or "").replace("\n", " "), 120)
            compact["max_matches"] = arguments.get("max_matches", 5)
            if arguments.get("section_type") is not None:
                compact["section_type"] = arguments.get("section_type")
        return {key: value for key, value in compact.items() if value is not None and value != ""}

    if tool_name == "get_section_details":
        compact["include_neighbors"] = bool(arguments.get("include_neighbors", True))
        if arguments.get("section_type") is not None:
            compact["section_type"] = arguments.get("section_type")
        if arguments.get("section_index") is not None:
            compact["section_index"] = arguments.get("section_index")
        return compact

    if tool_name == "replace_text_in_plan":
        compact["target_text"] = _truncate_text(str(arguments.get("target_text") or "").replace("\n", " "), 100)
        compact["replacement_text"] = _truncate_text(
            str(arguments.get("replacement_text") or "").replace("\n", " "),
            100,
        )
        compact["replace_all"] = bool(arguments.get("replace_all", True))
        if arguments.get("section_type") is not None:
            compact["section_type"] = arguments.get("section_type")
        return compact

    if tool_name in {
        "replace_paragraphs_in_section",
        "insert_paragraphs_in_section",
        "delete_paragraphs_in_section",
    }:
        if arguments.get("section_type") is not None:
            compact["section_type"] = arguments.get("section_type")
        if arguments.get("section_index") is not None:
            compact["section_index"] = arguments.get("section_index")
        compact["start_paragraph_index"] = arguments.get("start_paragraph_index")
        compact["end_paragraph_index"] = arguments.get("end_paragraph_index")
        compact["position"] = arguments.get("position")
        compact["paragraph_index"] = arguments.get("paragraph_index")
        if tool_name != "delete_paragraphs_in_section":
            new_text = str(arguments.get("new_text") or "").strip()
            if new_text:
                compact["new_text_preview"] = _truncate_text(new_text.replace("\n", " "), 120)
        return {key: value for key, value in compact.items() if value is not None and value != ""}

    if tool_name == "evaluate_plan_suitability":
        compact["focus"] = str(arguments.get("focus") or "整体适配度").strip()
        return compact

    return compact


def _compact_operation_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Trim heavy tool results before storing them as operation logs."""
    compact: dict[str, Any] = {}
    for key in (
        "ok",
        "keyword",
        "target_text",
        "replacements",
        "new_duration",
        "inserted_index",
        "inserted_paragraph_index",
        "start_paragraph_index",
        "end_paragraph_index",
        "new_index",
        "focus",
        "difficulty",
        "beginner_suitability",
        "section_type",
        "target_section",
        "deleted_section",
    ):
        if key in result:
            compact[key] = result.get(key)

    message = str(result.get("message") or "").strip()
    if message:
        compact["message"] = _truncate_text(message.replace("\n", " "), 240)

    if tool_name == "add_image_placeholder":
        description = str(result.get("description") or "").strip()
        placeholder = str(result.get("placeholder") or "").strip()
        if description:
            compact["description"] = _truncate_text(description, 100)
        if placeholder:
            compact["placeholder_preview"] = _truncate_text(placeholder, 100)
        return compact

    if tool_name in {"search_in_plan", "get_text_context_in_plan"}:
        matches = result.get("matches", [])
        if isinstance(matches, list):
            compact["matches_count"] = len(matches)
            compact["matches_preview"] = [_compact_match_payload(item) for item in matches[:3]]
        return compact

    if tool_name == "get_section_details":
        section = result.get("section")
        if isinstance(section, dict):
            compact["section"] = _compact_section_payload(section)
        return compact

    if tool_name == "evaluate_plan_suitability":
        prerequisites = result.get("prerequisites", [])
        reasons = result.get("reasons", [])
        if isinstance(prerequisites, list) and prerequisites:
            compact["prerequisites"] = [str(item).strip() for item in prerequisites[:3] if str(item).strip()]
        if isinstance(reasons, list) and reasons:
            compact["reasons"] = [_truncate_text(str(item).replace("\n", " "), 120) for item in reasons[:3] if str(item).strip()]
        return compact

    return {key: value for key, value in compact.items() if value is not None and value != "" and value != []}


def _record_success(
    op_service: OperationService,
    conversation_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist a successful tool operation when conversation context exists."""
    if conversation_id:
        op_service.create(
            OperationCreate(
                conversation_id=conversation_id,
                tool_name=tool_name,
                arguments=_compact_operation_arguments(tool_name, arguments),
                result=_compact_operation_result(tool_name, result),
            )
        )
        return {**result, "_operation_logged": True}
    return result


def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
    """Abort before persisting changes for a disconnected request."""
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()


def _save_plan_content(
    plan_service: PlanService,
    plan_id: str,
    content: dict[str, Any],
    *,
    cancel_token: CancellationToken | None = None,
) -> bool:
    """Persist plan content unless the surrounding request has been cancelled."""
    _raise_if_cancelled(cancel_token)
    updated_plan = plan_service.update(plan_id, PlanUpdate(content=content))
    return updated_plan is not None


def _walk_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Collect string leaves from a nested structure."""
    matches: list[tuple[str, str]] = []
    if isinstance(value, str):
        matches.append((path or "root", value))
        return matches
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            matches.extend(_walk_strings(item, next_path))
        return matches
    if isinstance(value, list):
        for index, item in enumerate(value):
            next_path = f"{path}[{index}]" if path else f"[{index}]"
            matches.extend(_walk_strings(item, next_path))
    return matches


def _make_snippet(text: str, keyword: str, radius: int = 20) -> str:
    """Return a short search snippet around the keyword."""
    lower_text = text.lower()
    lower_keyword = keyword.lower()
    position = lower_text.find(lower_keyword)
    if position < 0:
        return text[: radius * 2]
    start = max(position - radius, 0)
    end = min(position + len(keyword) + radius, len(text))
    return text[start:end]


def _resolve_section(
    sections: list[dict[str, Any]],
    *,
    section_type: str | None = None,
    section_index: int | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Resolve one section by type or explicit index."""
    resolved_index = _resolve_section_index(sections, section_type, section_index)
    if resolved_index is None:
        return None, None
    section = sections[resolved_index]
    if not isinstance(section, dict):
        return None, None
    return resolved_index, section


def _get_section_paragraphs(section: dict[str, Any]) -> tuple[list[str], str] | None:
    """Return textual paragraphs for a section, or None when content is not text."""
    raw_content = section.get("content")
    if raw_content is None:
        return [], "\n"
    if not isinstance(raw_content, str):
        return None
    return _split_content_blocks(raw_content)


def _build_neighbor_summary(sections: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    """Build a short summary for an adjacent section."""
    if not (0 <= index < len(sections)):
        return None
    section = sections[index]
    if not isinstance(section, dict):
        return None
    return {
        "section_index": index,
        "section_type": _section_label(section, f"章节{index + 1}"),
        "duration": section.get("duration"),
        "content_preview": str(section.get("content") or "").strip()[:200],
    }


def _build_section_details_payload(
    sections: list[dict[str, Any]],
    section_index: int,
    section: dict[str, Any],
    *,
    include_neighbors: bool,
) -> dict[str, Any]:
    """Build a rich section payload for precise agent reasoning."""
    paragraph_payload: list[dict[str, Any]] = []
    paragraph_bundle = _get_section_paragraphs(section)
    if paragraph_bundle is not None:
        paragraphs, _ = paragraph_bundle
        paragraph_payload = [
            {"index": index, "text": text}
            for index, text in enumerate(paragraphs)
        ]

    payload = {
        "section_index": section_index,
        "section_type": _section_label(section, f"章节{section_index + 1}"),
        "duration": section.get("duration"),
        "content": section.get("content"),
        "paragraphs": paragraph_payload,
        "elements": deepcopy(section.get("elements") or []),
        "raw_section": deepcopy(section),
    }
    if include_neighbors:
        payload["previous_section"] = _build_neighbor_summary(sections, section_index - 1)
        payload["next_section"] = _build_neighbor_summary(sections, section_index + 1)
    return payload


def _replace_text_in_value(
    value: Any,
    target_text: str,
    replacement_text: str,
    *,
    replace_all: bool,
) -> tuple[Any, int]:
    """Replace occurrences inside nested JSON-like values."""
    if isinstance(value, str):
        occurrences = value.count(target_text)
        if occurrences == 0:
            return value, 0
        if replace_all:
            return value.replace(target_text, replacement_text), occurrences
        return value.replace(target_text, replacement_text, 1), 1

    if isinstance(value, list):
        replaced_items: list[Any] = []
        total = 0
        for item in value:
            if total > 0 and not replace_all:
                replaced_items.append(item)
                continue
            updated_item, count = _replace_text_in_value(
                item,
                target_text,
                replacement_text,
                replace_all=replace_all,
            )
            replaced_items.append(updated_item)
            total += count
        return replaced_items, total

    if isinstance(value, dict):
        replaced_dict: dict[str, Any] = {}
        total = 0
        for key, item in value.items():
            if total > 0 and not replace_all:
                replaced_dict[str(key)] = item
                continue
            updated_item, count = _replace_text_in_value(
                item,
                target_text,
                replacement_text,
                replace_all=replace_all,
            )
            replaced_dict[str(key)] = updated_item
            total += count
        return replaced_dict, total

    return value, 0


def rewrite_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Rewrite a section's content and persist the lesson plan."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs["section_type"]
    new_content = kwargs["new_content"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        section_index = _find_section_index(sections, section_type)
        if section_index is None:
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        sections[section_index]["content"] = new_content
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"修改失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        result = {
            "ok": True,
            "message": f"已成功重写{section_type}章节。",
            "section_type": section_type,
        }
        return _record_success(op_service, conversation_id, "rewrite_section", kwargs, result)


def adjust_duration_tool(**kwargs: Any) -> dict[str, Any]:
    """Adjust a section duration and validate the total lesson time."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs["section_type"]
    new_duration = kwargs["new_duration"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        section_index = _find_section_index(sections, section_type)
        if section_index is None:
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        sections[section_index]["duration"] = new_duration
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"调整失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        result = {
            "ok": True,
            "message": f"已将{section_type}章节时长调整为{new_duration}分钟。",
            "section_type": section_type,
            "new_duration": new_duration,
        }
        return _record_success(op_service, conversation_id, "adjust_duration", kwargs, result)


def insert_element_tool(**kwargs: Any) -> dict[str, Any]:
    """Insert a teaching element into the target section."""
    plan_id = kwargs["plan_id"]
    target_section = kwargs["target_section"]
    position = kwargs["position"]
    element_type = kwargs["element_type"]
    element_content = kwargs["content"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        section_index = _find_section_index(sections, target_section)
        if section_index is None:
            return {"ok": False, "message": f"错误：未找到章节 {target_section}。"}

        section = sections[section_index]
        elements = section.get("elements")
        if not isinstance(elements, list):
            elements = []
            section["elements"] = elements

        new_element = {"type": element_type, "content": element_content}
        if position == "start":
            elements.insert(0, new_element)
        else:
            elements.append(new_element)
        _update_textual_content(section, position, _render_element_text(element_type, element_content))

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        result = {
            "ok": True,
            "message": f"已在{target_section}章节插入{element_type}元素。",
            "target_section": target_section,
            "position": position,
        }
        return _record_success(op_service, conversation_id, "insert_element", kwargs, result)


def insert_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Insert a new section into the lesson plan."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs["section_type"]
    section_content = kwargs["content"]
    duration = kwargs.get("duration", 0)
    position = kwargs.get("position", "end")
    reference_section = kwargs.get("reference_section")
    reference_index = kwargs.get("reference_index")
    elements = kwargs.get("elements") or []
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        new_section: dict[str, Any] = {
            "type": section_type,
            "content": section_content,
            "duration": duration,
        }
        if isinstance(elements, list) and elements:
            new_section["elements"] = deepcopy(elements)

        insert_at = len(sections)
        if position == "start":
            insert_at = 0
        elif position == "end":
            insert_at = len(sections)
        else:
            resolved_index = _resolve_section_index(sections, reference_section, reference_index)
            if resolved_index is None:
                return {"ok": False, "message": "错误：未找到用于定位的新章节参考位置。"}
            insert_at = resolved_index if position == "before" else resolved_index + 1

        sections.insert(insert_at, new_section)
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"插入失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        result = {
            "ok": True,
            "message": f"已新增章节：{section_type}。",
            "section_type": section_type,
            "inserted_index": insert_at,
        }
        return _record_success(op_service, conversation_id, "insert_section", kwargs, result)


def add_image_placeholder_tool(**kwargs: Any) -> dict[str, Any]:
    """Insert a Markdown image placeholder into a target section."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs["section_type"]
    position = kwargs["position"]
    description = kwargs["description"].strip()
    paragraph_index = kwargs.get("paragraph_index")
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}
        if not description:
            return {"ok": False, "message": "错误：图片描述不能为空。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        section_index = _find_section_index(sections, section_type)
        if section_index is None:
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        section = sections[section_index]
        placeholder = _build_image_placeholder(description)
        paragraphs, separator = _split_content_blocks(section.get("content"))

        if position == "start":
            updated_paragraphs = [placeholder, *paragraphs]
        elif position == "end":
            updated_paragraphs = [*paragraphs, placeholder]
        elif position == "after_paragraph":
            if paragraph_index is None:
                return {"ok": False, "message": "错误：position 为 after_paragraph 时必须提供 paragraph_index。"}
            if paragraph_index < 0 or paragraph_index >= len(paragraphs):
                return {"ok": False, "message": f"错误：段落索引 {paragraph_index} 超出范围。"}
            updated_paragraphs = paragraphs[: paragraph_index + 1] + [placeholder] + paragraphs[paragraph_index + 1 :]
        else:
            return {"ok": False, "message": f"错误：不支持的插入位置 {position}。"}

        section["content"] = separator.join(updated_paragraphs) if updated_paragraphs else placeholder

        elements = section.get("elements")
        if not isinstance(elements, list):
            elements = []
            section["elements"] = elements

        element = {
            "type": "image_placeholder",
            "description": description,
            "position": position,
            "placeholder": placeholder,
            "status": "pending_upload",
        }
        if paragraph_index is not None:
            element["paragraph_index"] = paragraph_index
        if position == "start":
            elements.insert(0, element)
        else:
            elements.append(element)

        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"插入失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        result = {
            "ok": True,
            "message": f"已在{section_type}章节插入图片占位符。",
            "section_type": section_type,
            "position": position,
            "description": description,
            "placeholder": placeholder,
        }
        return _record_success(op_service, conversation_id, "add_image_placeholder", kwargs, result)


def delete_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Delete a lesson-plan section by type or index."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]

        resolved_index = section_index
        if resolved_index is None and section_type is not None:
            resolved_index = _find_section_index(sections, section_type)
        if resolved_index is None or resolved_index < 0 or resolved_index >= len(sections):
            return {"ok": False, "message": "错误：未找到要删除的章节。"}

        removed_section = sections.pop(resolved_index)
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"删除失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        removed_name = (
            removed_section.get("type")
            or removed_section.get("section_type")
            or removed_section.get("title")
            or removed_section.get("name")
            or str(resolved_index)
        )
        result = {
            "ok": True,
            "message": f"已删除章节：{removed_name}。",
            "deleted_section": removed_name,
        }
        return _record_success(op_service, conversation_id, "delete_section", kwargs, result)


def move_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Reorder a section to a new index."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    new_index = kwargs["new_index"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        resolved_index = _resolve_section_index(sections, section_type, section_index)
        if resolved_index is None:
            return {"ok": False, "message": "错误：未找到要移动的章节。"}
        if new_index >= len(sections):
            return {"ok": False, "message": f"错误：目标索引 {new_index} 超出范围。"}

        section = sections.pop(resolved_index)
        insert_at = new_index if new_index <= resolved_index else new_index
        sections.insert(insert_at, section)

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        section_name = _section_label(section, str(insert_at))
        result = {
            "ok": True,
            "message": f"已将章节“{section_name}”移动到第 {insert_at + 1} 个位置。",
            "section_type": section_name,
            "new_index": insert_at,
        }
        return _record_success(op_service, conversation_id, "move_section", kwargs, result)


def search_in_plan_tool(**kwargs: Any) -> dict[str, Any]:
    """Search keyword matches across the lesson-plan JSON."""
    plan_id = kwargs["plan_id"]
    keyword = kwargs["keyword"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        matches: list[dict[str, Any]] = []

        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            section_name = (
                section.get("type")
                or section.get("section_type")
                or section.get("title")
                or section.get("name")
                or f"section_{index}"
            )
            for path, text in _walk_strings(section):
                if keyword.lower() not in text.lower():
                    continue
                matches.append(
                    {
                        "section": section_name,
                        "path": path,
                        "snippet": _make_snippet(text, keyword),
                    }
                )

        result = {
            "ok": True,
            "message": f"找到 {len(matches)} 处与“{keyword}”相关的内容。",
            "keyword": keyword,
            "matches": matches[:10],
        }
        return _record_success(op_service, conversation_id, "search_in_plan", kwargs, result)


def get_section_details_tool(**kwargs: Any) -> dict[str, Any]:
    """Return one section with paragraph breakdown and adjacent-section summaries."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    include_neighbors = kwargs.get("include_neighbors", True)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        resolved_index, section = _resolve_section(
            sections,
            section_type=section_type,
            section_index=section_index,
        )
        if resolved_index is None or section is None:
            locator = section_type if section_type is not None else section_index
            return {"ok": False, "message": f"错误：未找到章节 {locator}。"}

        details = _build_section_details_payload(
            sections,
            resolved_index,
            section,
            include_neighbors=bool(include_neighbors),
        )
        result = {
            "ok": True,
            "message": f"已读取章节“{details['section_type']}”的详细上下文。",
            "section": details,
        }
        return _record_success(op_service, conversation_id, "get_section_details", kwargs, result)


def get_text_context_in_plan_tool(**kwargs: Any) -> dict[str, Any]:
    """Locate an exact text snippet and return richer surrounding context."""
    plan_id = kwargs["plan_id"]
    target_text = kwargs["target_text"]
    section_type = kwargs.get("section_type")
    max_matches = kwargs.get("max_matches", 5)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        candidate_sections = sections
        if section_type is not None:
            resolved_index, section = _resolve_section(sections, section_type=section_type)
            if resolved_index is None or section is None:
                return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}
            candidate_sections = [section]
            candidate_offset = resolved_index
        else:
            candidate_offset = 0

        matches: list[dict[str, Any]] = []
        for local_index, section in enumerate(candidate_sections):
            if not isinstance(section, dict):
                continue
            actual_index = candidate_offset + local_index if section_type is not None else local_index
            label = _section_label(section, f"章节{actual_index + 1}")
            for path, text in _walk_strings(section):
                if target_text.lower() not in text.lower():
                    continue
                match: dict[str, Any] = {
                    "section_index": actual_index,
                    "section_type": label,
                    "path": path,
                    "snippet": _make_snippet(text, target_text, radius=60),
                }
                paragraph_bundle = _get_section_paragraphs(section)
                if path == "content" and paragraph_bundle is not None:
                    paragraphs, _ = paragraph_bundle
                    paragraph_matches = [
                        {"index": index, "text": paragraph}
                        for index, paragraph in enumerate(paragraphs)
                        if target_text.lower() in paragraph.lower()
                    ]
                    if paragraph_matches:
                        match["paragraph_matches"] = paragraph_matches[:3]
                matches.append(match)
                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        result = {
            "ok": True,
            "message": f"找到 {len(matches)} 处与目标原文相关的上下文。",
            "target_text": target_text,
            "matches": matches,
        }
        return _record_success(op_service, conversation_id, "get_text_context_in_plan", kwargs, result)


def replace_text_in_plan_tool(**kwargs: Any) -> dict[str, Any]:
    """Replace text across the whole plan or inside one section."""
    plan_id = kwargs["plan_id"]
    target_text = kwargs["target_text"]
    replacement_text = kwargs["replacement_text"]
    section_type = kwargs.get("section_type")
    replace_all = kwargs.get("replace_all", True)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]

        if section_type:
            resolved_index = _find_section_index(sections, section_type)
            if resolved_index is None:
                return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}
            updated_section, replacements = _replace_text_in_value(
                sections[resolved_index],
                target_text,
                replacement_text,
                replace_all=replace_all,
            )
            sections[resolved_index] = updated_section
        else:
            updated_content, replacements = _replace_text_in_value(
                content,
                target_text,
                replacement_text,
                replace_all=replace_all,
            )
            content = _ensure_plan_content(updated_content)

        if replacements <= 0:
            return {"ok": False, "message": f"未找到“{target_text}”相关内容，未执行替换。"}

        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"替换失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        scope = f"{section_type}章节" if section_type else "整个教案"
        result = {
            "ok": True,
            "message": f"已在{scope}中替换 {replacements} 处文本。",
            "target_text": target_text,
            "replacement_text": replacement_text,
            "replacements": replacements,
        }
        return _record_success(op_service, conversation_id, "replace_text_in_plan", kwargs, result)


def replace_paragraphs_in_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Replace one or more paragraphs inside a section."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    start_paragraph_index = kwargs["start_paragraph_index"]
    end_paragraph_index = kwargs.get("end_paragraph_index")
    new_text = kwargs["new_text"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        resolved_index, section = _resolve_section(
            sections,
            section_type=section_type,
            section_index=section_index,
        )
        if resolved_index is None or section is None:
            locator = section_type if section_type is not None else section_index
            return {"ok": False, "message": f"错误：未找到章节 {locator}。"}

        paragraph_bundle = _get_section_paragraphs(section)
        if paragraph_bundle is None:
            return {"ok": False, "message": "错误：该章节当前不是纯文本内容，暂不支持按段落修改。"}
        paragraphs, separator = paragraph_bundle
        resolved_end = end_paragraph_index if end_paragraph_index is not None else start_paragraph_index
        if start_paragraph_index >= len(paragraphs) or resolved_end >= len(paragraphs):
            return {"ok": False, "message": "错误：目标段落索引超出范围。"}

        section["content"] = separator.join(
            [*paragraphs[:start_paragraph_index], new_text, *paragraphs[resolved_end + 1 :]]
        )
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"修改失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        label = _section_label(section, f"章节{resolved_index + 1}")
        result = {
            "ok": True,
            "message": f"已替换“{label}”中第 {start_paragraph_index + 1} 至 {resolved_end + 1} 段。",
            "section_type": label,
            "start_paragraph_index": start_paragraph_index,
            "end_paragraph_index": resolved_end,
        }
        return _record_success(op_service, conversation_id, "replace_paragraphs_in_section", kwargs, result)


def insert_paragraphs_in_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Insert one paragraph block into a section at a precise position."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    position = kwargs["position"]
    paragraph_index = kwargs.get("paragraph_index")
    new_text = kwargs["new_text"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        resolved_index, section = _resolve_section(
            sections,
            section_type=section_type,
            section_index=section_index,
        )
        if resolved_index is None or section is None:
            locator = section_type if section_type is not None else section_index
            return {"ok": False, "message": f"错误：未找到章节 {locator}。"}

        paragraph_bundle = _get_section_paragraphs(section)
        if paragraph_bundle is None:
            return {"ok": False, "message": "错误：该章节当前不是纯文本内容，暂不支持按段落插入。"}
        paragraphs, separator = paragraph_bundle

        if position == "start":
            insert_at = 0
        elif position == "end":
            insert_at = len(paragraphs)
        else:
            if paragraph_index is None or paragraph_index < 0 or paragraph_index >= len(paragraphs):
                return {"ok": False, "message": "错误：用于定位的 paragraph_index 超出范围。"}
            insert_at = paragraph_index if position == "before" else paragraph_index + 1

        section["content"] = separator.join([*paragraphs[:insert_at], new_text, *paragraphs[insert_at:]])
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"插入失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        label = _section_label(section, f"章节{resolved_index + 1}")
        result = {
            "ok": True,
            "message": f"已在“{label}”中插入新段落。",
            "section_type": label,
            "inserted_paragraph_index": insert_at,
            "position": position,
        }
        return _record_success(op_service, conversation_id, "insert_paragraphs_in_section", kwargs, result)


def delete_paragraphs_in_section_tool(**kwargs: Any) -> dict[str, Any]:
    """Delete one or more paragraphs inside a section."""
    plan_id = kwargs["plan_id"]
    section_type = kwargs.get("section_type")
    section_index = kwargs.get("section_index")
    start_paragraph_index = kwargs["start_paragraph_index"]
    end_paragraph_index = kwargs.get("end_paragraph_index")
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = content["sections"]
        resolved_index, section = _resolve_section(
            sections,
            section_type=section_type,
            section_index=section_index,
        )
        if resolved_index is None or section is None:
            locator = section_type if section_type is not None else section_index
            return {"ok": False, "message": f"错误：未找到章节 {locator}。"}

        paragraph_bundle = _get_section_paragraphs(section)
        if paragraph_bundle is None:
            return {"ok": False, "message": "错误：该章节当前不是纯文本内容，暂不支持按段落删除。"}
        paragraphs, separator = paragraph_bundle
        resolved_end = end_paragraph_index if end_paragraph_index is not None else start_paragraph_index
        if start_paragraph_index >= len(paragraphs) or resolved_end >= len(paragraphs):
            return {"ok": False, "message": "错误：目标段落索引超出范围。"}

        section["content"] = separator.join([*paragraphs[:start_paragraph_index], *paragraphs[resolved_end + 1 :]])
        valid, error_message = validate_plan(content)
        if not valid:
            return {"ok": False, "message": f"删除失败：{error_message}"}

        if not _save_plan_content(plan_service, plan_id, content, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：教案更新失败。"}

        label = _section_label(section, f"章节{resolved_index + 1}")
        result = {
            "ok": True,
            "message": f"已删除“{label}”中第 {start_paragraph_index + 1} 至 {resolved_end + 1} 段。",
            "section_type": label,
            "start_paragraph_index": start_paragraph_index,
            "end_paragraph_index": resolved_end,
        }
        return _record_success(op_service, conversation_id, "delete_paragraphs_in_section", kwargs, result)


def evaluate_plan_suitability_tool(**kwargs: Any) -> dict[str, Any]:
    """Evaluate overall lesson-plan difficulty and beginner suitability."""
    plan_id = kwargs["plan_id"]
    focus = str(kwargs.get("focus") or "整体适配度").strip()
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (plan_service, op_service):
        plan = plan_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：教案不存在。"}

        content = _ensure_plan_content(plan.content)
        sections = [section for section in content.get("sections", []) if isinstance(section, dict)]
        all_text = "\n".join(str(section.get("content") or "") for section in sections)
        total_duration = sum(
            int(section.get("duration") or 0)
            for section in sections
            if isinstance(section.get("duration"), (int, float)) and not isinstance(section.get("duration"), bool)
        )

        advanced_keywords = ("推导", "证明", "建模", "抽象", "综合", "迁移", "探究", "自主", "实验设计")
        beginner_keywords = ("认识", "初步", "感知", "体验", "观察", "例子", "基础", "入门")
        interactive_keywords = ("提问", "讨论", "活动", "练习", "操作", "反馈", "演示")

        advanced_hits = sum(1 for keyword in advanced_keywords if keyword in all_text)
        beginner_hits = sum(1 for keyword in beginner_keywords if keyword in all_text)
        interactive_hits = sum(1 for keyword in interactive_keywords if keyword in all_text)

        difficulty_score = 0
        if total_duration >= 45:
            difficulty_score += 1
        if len(sections) >= 5:
            difficulty_score += 1
        if advanced_hits >= 3:
            difficulty_score += 2
        elif advanced_hits >= 1:
            difficulty_score += 1
        if interactive_hits >= 2:
            difficulty_score -= 1
        if beginner_hits >= 2:
            difficulty_score -= 1

        if difficulty_score <= 0:
            difficulty = "低"
        elif difficulty_score <= 2:
            difficulty = "中"
        else:
            difficulty = "高"

        if difficulty == "低":
            beginner_suitability = "适合直接作为第一课入门。"
        elif difficulty == "中":
            beginner_suitability = "基本适合作为第一课，但建议先补充更直观的导入或示例。"
        else:
            beginner_suitability = "不太适合作为零基础第一课，建议先拆分前置知识。"

        prerequisites: list[str] = []
        if advanced_hits:
            prerequisites.append("需要一定的前置概念铺垫")
        if total_duration > 40:
            prerequisites.append("课堂节奏偏紧，需控制活动密度")
        if not prerequisites:
            prerequisites.append("前置要求较低，可边学边建立概念")

        reasons = [
            f"共 {len(sections)} 个章节，总时长约 {total_duration} 分钟。",
            f"互动性线索 {interactive_hits} 处，高阶认知线索 {advanced_hits} 处。",
            f"入门友好线索 {beginner_hits} 处。",
        ]

        result = {
            "ok": True,
            "focus": focus,
            "difficulty": difficulty,
            "beginner_suitability": beginner_suitability,
            "prerequisites": prerequisites,
            "reasons": reasons,
            "message": (
                f"围绕“{focus}”的评估结果：当前教案整体难度{difficulty}。"
                f"{beginner_suitability} 前置要求：{'；'.join(prerequisites)}"
            ),
        }
        return _record_success(op_service, conversation_id, "evaluate_plan_suitability", kwargs, result)


LESSON_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="rewrite_section",
        description="重写教案指定章节的内容。适用于把导入、新授、练习、总结等章节整体改写为新的文本。",
        args_schema=RewriteSectionArgs,
        func=rewrite_section_tool,
    ),
    Tool(
        name="adjust_duration",
        description="调整某个章节的时长，并检查教案总时长是否仍在合理范围内。",
        args_schema=AdjustDurationArgs,
        func=adjust_duration_tool,
    ),
    Tool(
        name="insert_element",
        description="在指定章节插入教学元素，如提问、案例、活动或板书，支持开头、结尾或首段后插入。",
        args_schema=InsertElementArgs,
        func=insert_element_tool,
    ),
    Tool(
        name="insert_section",
        description="新增一个完整章节，可插入到教案开头、结尾或某个已有章节的前后。",
        args_schema=InsertSectionArgs,
        func=insert_section_tool,
    ),
    Tool(
        name="delete_section",
        description="按章节类型或索引删除教案中的一个章节，并确保教案至少保留一个章节。",
        args_schema=DeleteSectionArgs,
        func=delete_section_tool,
    ),
    Tool(
        name="move_section",
        description="调整章节顺序，把某个章节移动到新的索引位置。",
        args_schema=MoveSectionArgs,
        func=move_section_tool,
    ),
    Tool(
        name="add_image_placeholder",
        description="在教案指定章节插入图片占位符，支持章节开头、结尾或指定段落之后插入。",
        args_schema=AddImagePlaceholderArgs,
        func=add_image_placeholder_tool,
    ),
    Tool(
        name="search_in_plan",
        description="在当前教案 JSON 中搜索关键词，返回匹配章节、路径和内容片段。",
        args_schema=SearchInPlanArgs,
        func=search_in_plan_tool,
    ),
    Tool(
        name="get_section_details",
        description="读取指定章节的完整编辑上下文，返回段落拆分、结构化元素和相邻章节摘要，适合改写前先定位。",
        args_schema=GetSectionDetailsArgs,
        func=get_section_details_tool,
    ),
    Tool(
        name="get_text_context_in_plan",
        description="按原文片段精确定位教案中的命中位置，并返回所在章节、字段路径和附近文本上下文。",
        args_schema=GetTextContextInPlanArgs,
        func=get_text_context_in_plan_tool,
    ),
    Tool(
        name="replace_text_in_plan",
        description="在整个教案或指定章节中批量替换文本，适合术语统一、措辞调整和局部删改。",
        args_schema=ReplaceTextInPlanArgs,
        func=replace_text_in_plan_tool,
    ),
    Tool(
        name="replace_paragraphs_in_section",
        description="按段落索引替换某个章节中的一段或连续多段，适合局部重写而不影响整章其他内容。",
        args_schema=ReplaceParagraphsInSectionArgs,
        func=replace_paragraphs_in_section_tool,
    ),
    Tool(
        name="insert_paragraphs_in_section",
        description="在指定章节的开头、结尾或某段前后插入新段落，适合补充说明、案例或过渡句。",
        args_schema=InsertParagraphsInSectionArgs,
        func=insert_paragraphs_in_section_tool,
    ),
    Tool(
        name="delete_paragraphs_in_section",
        description="按段落索引删除某个章节中的一段或连续多段，适合精确删改冗余内容。",
        args_schema=DeleteParagraphsInSectionArgs,
        func=delete_paragraphs_in_section_tool,
    ),
    Tool(
        name="evaluate_plan_suitability",
        description="从整体上评估教案难度、入门适配度和前置知识要求，适合回答“是否适合作为第一课”等问题。",
        args_schema=EvaluatePlanSuitabilityArgs,
        func=evaluate_plan_suitability_tool,
    ),
)


def register_lesson_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register lesson-plan editing tools into the target registry."""
    return register_tools_once(registry, (*LESSON_TOOLS, *CONTROL_FLOW_TOOLS))
