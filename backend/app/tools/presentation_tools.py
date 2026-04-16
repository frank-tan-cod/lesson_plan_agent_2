"""Presentation-specific tool definitions and registration."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from difflib import SequenceMatcher
import re
from typing import Any, Iterator
import unicodedata

from pydantic import BaseModel, Field, model_validator

from backend.tools import Tool
from backend.tools.cancellation import CancellationToken
from backend.tools.registry import ToolsRegistry

from ..core.settings import settings
from ..database import session_maker
from ..presentation_layouts import get_presentation_template, resolve_template_layout_name
from ..presentation_models import PresentationDocument, Slide, normalize_slide_template
from ..schemas import (
    MiniGamePayload,
    OperationCreate,
    PresentationUpdate,
)
from ..services.operation_service import OperationService
from ..services.plan_service import PlanService
from ..services.presentation_service import PresentationService
from .control_flow_tools import CONTROL_FLOW_TOOLS
from .registry_utils import register_tools_once

NONFINAL_SLIDE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:需要|先)(?:搜索|定位|确认|查询).{0,18}(?:后|再).{0,8}(?:填充|修改|更新|写入)"),
    re.compile(r"准备将内容(?:合并|写入|更新)到第\d+页"),
    re.compile(r"删除合并后的冗余页面"),
    re.compile(r"待确认操作"),
    re.compile(r"^(?:待补充|待完善|占位|占位文案|示意稿|示意版|todo)$", flags=re.IGNORECASE),
)
NONFINAL_SLIDE_FIELD_LABELS = {
    "title": "标题",
    "subtitle": "副标题",
    "body": "正文",
    "notes": "备注",
    "image_description": "图片说明",
}
GAME_ENTRY_LINE_PATTERN = re.compile(r"^[\[\(【（]?\s*(互动入口|互动页面|游戏入口)\s*[：:]\s*(.+?)\s*[\]\)】）]?$")
GAME_LINK_PLACEHOLDER_PATTERN = re.compile(r"\[\[\s*(?:GAME_LINK|小游戏入口|游戏入口|互动入口)\s*[:#：]\s*(\d+)\s*\]\]", flags=re.IGNORECASE)
ABSOLUTE_URL_PATTERN = re.compile(r"(https?://[^\s]+)")
UPLOADS_URL_PATTERN = re.compile(r"((?:/?uploads/games/[^\s]+))")
GAME_LINK_HINT_MARKERS = ("小游戏", "互动", "游戏入口", "互动入口", "点击此处挑战", "点击打开")
GAME_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("对错", ("对与错", "对错")),
    ("找一找", ("找一找",)),
    ("排排序", ("排排序", "排序")),
    ("翻翻卡", ("翻翻卡", "翻翻乐")),
    ("选一选", ("选一选",)),
    ("抢答", ("抢答", "快答")),
)
GAME_MATCH_SIMILARITY = 0.52
GAME_MATCH_TITLE_SIMILARITY = 0.58


class AddSlideArgs(BaseModel):
    """Arguments for inserting a new slide."""

    template: str = Field(
        default="title_body",
        description="已注册模板名，例如 title_body、title_body_image、title_subtitle。",
    )
    title: str = Field(..., min_length=1, description="幻灯片标题。")
    subtitle: str | None = Field(default=None, description="可选副标题，适合封面或结束页。")
    body: str = Field(default="", description="课堂展示正文。")
    image_description: str | None = Field(default=None, description="图片占位说明，仅含图模板时建议填写。")
    after_slide_index: int | None = Field(
        default=None,
        description="插入到哪一页之后，-1 或留空表示追加到末尾；与 before_slide_index 二选一。",
    )
    before_slide_index: int | None = Field(
        default=None,
        description="插入到哪一页之前，例如 0 表示插入到第一页前；与 after_slide_index 二选一。",
    )

    @model_validator(mode="before")
    @classmethod
    def map_legacy_layout(cls, value: Any) -> Any:
        """Accept older `layout` calls and map them onto registered templates."""
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("template") is None and payload.get("layout") is not None:
            payload["template"] = normalize_slide_template(None, payload.get("layout"))
        return payload

    @model_validator(mode="after")
    def validate_position(self) -> "AddSlideArgs":
        """Allow exactly one positional mode while keeping append semantics backward-compatible."""
        if self.after_slide_index is not None and self.after_slide_index < -1:
            raise ValueError("after_slide_index 只能是 -1 或更大的整数。")
        if self.before_slide_index is not None and self.before_slide_index < 0:
            raise ValueError("before_slide_index 不能小于 0。")
        if self.after_slide_index is not None and self.before_slide_index is not None:
            raise ValueError("after_slide_index 和 before_slide_index 只能提供一个。")
        return self


class SetBulletPointsArgs(BaseModel):
    """Arguments for replacing bullet points on a slide."""

    slide_index: int = Field(..., ge=0, description="要修改的幻灯片索引，从 0 开始。")
    points: list[str] = Field(default_factory=list, description="新的要点列表。")


class ChangeLayoutArgs(BaseModel):
    """Arguments for changing a slide layout."""

    slide_index: int = Field(..., ge=0, description="目标幻灯片索引。")
    new_layout: str = Field(..., min_length=1, description="新的版式名称，会映射到当前已注册模板；切到非图片版式时会移除图片占位字段。")


class ReplacePresentationArgs(BaseModel):
    """Arguments for replacing the whole presentation in one call."""

    title: str | None = Field(default=None, description="新的演示文稿标题。")
    classroom_script: str | None = Field(default=None, description="新的课堂内容稿。")
    slides: list[Slide] = Field(default_factory=list, description="完整的幻灯片列表，会整体替换原内容。")

    @model_validator(mode="after")
    def validate_slides(self) -> "ReplacePresentationArgs":
        """Reject empty deck rewrites that would wipe the presentation."""
        if not self.slides:
            raise ValueError("slides 不能为空；整体替换演示文稿时至少提供 1 页幻灯片。")
        return self


class UpdateSlideContentArgs(BaseModel):
    """Arguments for updating a slide without replacing the whole deck."""

    slide_index: int = Field(..., ge=0, description="目标幻灯片索引，从 0 开始。")
    title: str | None = Field(default=None, description="新的页面标题。")
    subtitle: str | None = Field(default=None, description="新的页面副标题。")
    body: str | None = Field(default=None, description="新的页面正文。")
    game_index: int | None = Field(default=None, ge=1, description="可选：绑定第几个小游戏入口，从 1 开始。")
    template: str | None = Field(default=None, description="新的已注册模板。")
    image_description: str | None = Field(default=None, description="新的图片占位说明。")
    image_url: str | None = Field(default=None, description="新的图片文件路径。")
    notes: str | None = Field(default=None, description="新的讲解备注。")
    source_section: str | None = Field(default=None, description="新的教案环节标记。")

    @model_validator(mode="before")
    @classmethod
    def map_legacy_layout(cls, value: Any) -> Any:
        """Accept older `layout` updates and convert them into template updates."""
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("template") is None and payload.get("layout") is not None:
            payload["template"] = normalize_slide_template(None, payload.get("layout"))
        return payload

    @model_validator(mode="after")
    def validate_update_fields(self) -> "UpdateSlideContentArgs":
        """Require at least one field to update."""
        if not any(
            value is not None
            for value in (
                self.title,
                self.subtitle,
                self.body,
                self.game_index,
                self.template,
                self.image_description,
                self.image_url,
                self.notes,
                self.source_section,
            )
        ):
            raise ValueError("至少提供一个要更新的字段。")
        return self


class AddNotesArgs(BaseModel):
    """Arguments for editing speaker notes."""

    slide_index: int = Field(..., ge=0, description="目标幻灯片索引。")
    notes: str = Field(..., min_length=1, description="演讲者备注。")


class DuplicateSlideArgs(BaseModel):
    """Arguments for duplicating a slide."""

    slide_index: int = Field(..., ge=0, description="要复制的幻灯片索引。")


class MoveSlideArgs(BaseModel):
    """Arguments for moving a slide to a new index."""

    slide_index: int = Field(..., ge=0, description="要移动的原始幻灯片索引。")
    new_index: int = Field(..., ge=0, description="移动后的目标索引，按移除原页后的新顺序计算。")


class DeleteSlideArgs(BaseModel):
    """Arguments for deleting a slide."""

    slide_index: int = Field(..., ge=0, description="要删除的幻灯片索引。")


class GetPresentationOutlineArgs(BaseModel):
    """Arguments for reading the deck outline."""

    max_slides: int = Field(default=12, ge=1, le=50, description="最多返回多少页概要。")
    include_classroom_script: bool = Field(default=False, description="是否附带课堂内容稿摘要。")


class GetSlideDetailsArgs(BaseModel):
    """Arguments for reading one slide in detail."""

    slide_index: int | None = Field(default=None, ge=0, description="目标页码索引，从 0 开始。")
    title_keyword: str | None = Field(default=None, description="可选：按标题关键词定位。")
    include_neighbors: bool = Field(default=True, description="是否附带前后相邻页摘要。")

    @model_validator(mode="after")
    def validate_target(self) -> "GetSlideDetailsArgs":
        """Require either slide index or title keyword."""
        if self.slide_index is None and not str(self.title_keyword or "").strip():
            raise ValueError("至少提供 slide_index 或 title_keyword。")
        return self


class SearchInPresentationArgs(BaseModel):
    """Arguments for keyword search within the deck."""

    keyword: str = Field(..., min_length=1, description="要搜索的关键词或原句片段。")
    max_matches: int = Field(default=5, ge=1, le=20, description="最多返回多少条命中。")


@contextmanager
def _tool_services(user_id: str) -> Iterator[tuple[PresentationService, OperationService]]:
    """Build fresh synchronous services for tool execution."""
    with session_maker() as session:
        yield PresentationService(session, user_id=user_id), OperationService(session, user_id=user_id)


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


def _compact_operation_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep only the most useful and compact operation arguments for persistence."""
    compact: dict[str, Any] = {}
    if "plan_id" in arguments:
        compact["plan_id"] = arguments["plan_id"]

    if tool_name == "add_slide":
        compact.update(
            {
                "after_slide_index": arguments.get("after_slide_index"),
                "before_slide_index": arguments.get("before_slide_index"),
                "template": _coerce_requested_template(arguments.get("template"), arguments.get("layout")),
                "title": str(arguments.get("title") or "").strip(),
            }
        )
        subtitle = str(arguments.get("subtitle") or "").strip()
        body = str(arguments.get("body") or "").strip()
        image_description = str(arguments.get("image_description") or "").strip()
        if subtitle:
            compact["subtitle_preview"] = _truncate_text(subtitle, 80)
        if body:
            compact["body_preview"] = _truncate_text(body.replace("\n", " "), 120)
        if image_description:
            compact["image_description_preview"] = _truncate_text(image_description, 80)
        return compact

    if tool_name == "set_bullet_points":
        points = [str(item).strip() for item in arguments.get("points", []) if str(item).strip()]
        compact.update(
            {
                "slide_index": arguments.get("slide_index"),
                "points_count": len(points),
            }
        )
        if points:
            compact["points_preview"] = [_truncate_text(point, 60) for point in points[:3]]
        return compact

    if tool_name in {"change_layout", "duplicate_slide", "move_slide", "delete_slide"}:
        compact["slide_index"] = arguments.get("slide_index")
        if tool_name == "change_layout":
            compact["new_layout"] = str(arguments.get("new_layout") or "").strip()
        if tool_name == "move_slide":
            compact["new_index"] = arguments.get("new_index")
        return compact

    if tool_name == "add_notes":
        compact["slide_index"] = arguments.get("slide_index")
        notes = str(arguments.get("notes") or "").strip()
        if notes:
            compact["notes_preview"] = _truncate_text(notes.replace("\n", " "), 120)
        return compact

    if tool_name == "replace_presentation":
        slides = arguments.get("slides", [])
        compact["slides_count"] = len(slides) if isinstance(slides, list) else 0
        title = str(arguments.get("title") or "").strip()
        classroom_script = str(arguments.get("classroom_script") or "").strip()
        if title:
            compact["title"] = title
        if classroom_script:
            compact["classroom_script_preview"] = _truncate_text(classroom_script.replace("\n", " "), 140)
        if isinstance(slides, list) and slides:
            compact["slides_preview"] = [_compact_slide_payload(item, include_content=False) for item in slides[:3]]
        return compact

    if tool_name == "update_slide_content":
        compact["slide_index"] = arguments.get("slide_index")
        for field in ("title", "subtitle", "template", "layout", "source_section"):
            value = str(arguments.get(field) or "").strip()
            if value:
                compact[field] = value
        for field, target_key in (
            ("body", "body_preview"),
            ("notes", "notes_preview"),
            ("image_description", "image_description_preview"),
            ("image_url", "image_url"),
        ):
            value = str(arguments.get(field) or "").strip()
            if value:
                compact[target_key] = _truncate_text(value.replace("\n", " "), 140)
        return compact

    if tool_name == "get_presentation_outline":
        compact["max_slides"] = arguments.get("max_slides", 12)
        compact["include_classroom_script"] = bool(arguments.get("include_classroom_script", False))
        return compact

    if tool_name == "get_slide_details":
        compact["include_neighbors"] = bool(arguments.get("include_neighbors", True))
        if arguments.get("slide_index") is not None:
            compact["slide_index"] = arguments.get("slide_index")
        title_keyword = str(arguments.get("title_keyword") or "").strip()
        if title_keyword:
            compact["title_keyword"] = title_keyword
        return compact

    if tool_name == "search_in_presentation":
        compact["keyword"] = str(arguments.get("keyword") or "").strip()
        compact["max_matches"] = arguments.get("max_matches", 5)
        return compact

    return compact


def _compact_operation_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Trim large result payloads before storing them as operation logs."""
    compact: dict[str, Any] = {}
    for key in (
        "ok",
        "keyword",
        "match_mode",
        "slides_count",
        "slide_index",
        "source_slide_index",
        "new_slide_index",
        "from_slide_index",
        "to_slide_index",
    ):
        if key in result:
            compact[key] = result.get(key)

    message = str(result.get("message") or "").strip()
    if message:
        compact["message"] = _truncate_text(message.replace("\n", " "), 240)

    if tool_name == "add_slide":
        compact["title"] = result.get("title")
        compact["template"] = result.get("template")
        return compact

    if tool_name == "set_bullet_points":
        bullet_points = [str(item).strip() for item in result.get("bullet_points", []) if str(item).strip()]
        compact["bullet_points_count"] = len(bullet_points)
        if bullet_points:
            compact["bullet_points_preview"] = [_truncate_text(point, 60) for point in bullet_points[:3]]
        return compact

    if tool_name in {"change_layout", "update_slide_content"}:
        compact["template"] = result.get("template")
        if "title" in result:
            compact["title"] = result.get("title")
        subtitle = str(result.get("subtitle") or "").strip()
        if subtitle:
            compact["subtitle_preview"] = _truncate_text(subtitle, 80)
        compact["cleared_image_fields"] = bool(result.get("cleared_image_fields", False))
        return compact

    if tool_name == "add_notes":
        notes = str(result.get("notes") or "").strip()
        if notes:
            compact["notes_preview"] = _truncate_text(notes.replace("\n", " "), 120)
        return compact

    if tool_name == "delete_slide":
        compact["deleted_title"] = result.get("deleted_title")
        return compact

    if tool_name == "move_slide":
        compact["from_slide_index"] = result.get("from_slide_index")
        compact["to_slide_index"] = result.get("to_slide_index")
        compact["title"] = result.get("title")
        return compact

    if tool_name == "replace_presentation":
        compact["title"] = result.get("title")
        return compact

    if tool_name == "get_presentation_outline":
        slides = result.get("slides", [])
        title = str(result.get("title") or "").strip()
        if title:
            compact["title"] = title
        if isinstance(slides, list) and slides:
            compact["slides_preview"] = [_compact_slide_payload(item, include_content=False) for item in slides[:3]]
        return compact

    if tool_name == "get_slide_details":
        slide = result.get("slide")
        if isinstance(slide, dict):
            compact["slide"] = _compact_slide_payload(slide, include_content=True)
        return compact

    if tool_name == "search_in_presentation":
        matches = result.get("matches", [])
        if isinstance(matches, list):
            compact["matches_count"] = len(matches)
            compact["matches_preview"] = [_compact_match_payload(item) for item in matches[:3] if isinstance(item, dict)]
        return compact

    return compact


def _compact_slide_payload(payload: Any, *, include_content: bool) -> dict[str, Any]:
    """Turn a raw slide-like payload into a short, searchable summary."""
    if isinstance(payload, Slide):
        raw = payload.model_dump()
    elif hasattr(payload, "model_dump"):
        raw = payload.model_dump()
    elif isinstance(payload, dict):
        raw = payload
    else:
        return {"summary": _truncate_text(str(payload), 120)}

    compact = {
        "slide_index": raw.get("slide_index"),
        "title": raw.get("title"),
        "template": raw.get("template"),
    }
    if raw.get("game_index") is not None:
        compact["game_index"] = raw.get("game_index")
    subtitle = str(raw.get("subtitle") or "").strip()
    if subtitle:
        compact["subtitle_preview"] = _truncate_text(subtitle, 80)

    if include_content:
        body = str(raw.get("body") or "").strip()
        if body:
            compact["body_preview"] = _truncate_text(body.replace("\n", " "), 140)
        bullet_points = [str(item).strip() for item in raw.get("bullet_points", []) if str(item).strip()]
        if bullet_points:
            compact["bullet_points_count"] = len(bullet_points)
            compact["bullet_points_preview"] = [_truncate_text(point, 60) for point in bullet_points[:3]]
        for field, target_key in (
            ("notes", "notes_preview"),
            ("image_description", "image_description_preview"),
            ("image_url", "image_url"),
            ("source_section", "source_section"),
        ):
            value = str(raw.get(field) or "").strip()
            if value:
                compact[target_key] = _truncate_text(value.replace("\n", " "), 120)

    preview = str(raw.get("preview") or "").strip()
    if preview:
        compact["preview"] = _truncate_text(preview, 120)

    return {key: value for key, value in compact.items() if value is not None and value != ""}


def _compact_match_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep search matches compact while preserving location and snippet context."""
    compact: dict[str, Any] = {}
    for key in ("slide_index", "title", "field", "match_type", "score"):
        if key in payload:
            compact[key] = payload.get(key)
    snippet = str(payload.get("snippet") or "").strip()
    if snippet:
        compact["snippet"] = _truncate_text(snippet.replace("\n", " "), 120)
    return compact


def _load_document(plan: Any) -> PresentationDocument:
    """Normalize the persisted plan content into a validated presentation document."""
    payload = deepcopy(plan.content) if isinstance(plan.content, dict) else {"slides": []}
    payload["title"] = payload.get("title") or plan.title
    payload.setdefault("classroom_script", "")
    payload.setdefault("slides", [])
    return PresentationDocument.model_validate(payload)


def _load_linkable_minigames(plan: Any, *, plan_service: PlanService) -> list[MiniGamePayload]:
    """Load generated mini-games from the lesson plan that produced this presentation."""
    metadata = plan.metadata_json if isinstance(getattr(plan, "metadata_json", None), dict) else {}
    source_plan_id = str(metadata.get("source_plan_id") or "").strip()
    if not source_plan_id:
        return []

    source_plan = plan_service.get(source_plan_id)
    if source_plan is None or source_plan.doc_type != "lesson":
        return []

    content = source_plan.content if isinstance(source_plan.content, dict) else {}
    raw_games = content.get("games")
    if not isinstance(raw_games, list):
        return []

    normalized: list[MiniGamePayload] = []
    for item in raw_games:
        try:
            game = MiniGamePayload.model_validate(item)
        except Exception:
            continue
        if game.html_url:
            normalized.append(game)
    return normalized


def _resolve_public_game_url(value: str | None) -> str | None:
    """Normalize stored relative game URLs to public URLs."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    if raw_value.startswith(("http://", "https://")):
        return raw_value
    if raw_value.startswith("/"):
        return f"{settings.PUBLIC_BASE_URL}{raw_value}"
    if raw_value.startswith("uploads/"):
        return f"{settings.PUBLIC_BASE_URL}/{raw_value}"
    return raw_value


def _normalize_optional_game_index(value: Any) -> int | None:
    """Coerce a loose game index into a usable positive integer."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _normalize_game_match_key(value: Any) -> str:
    """Normalize a game/slide label for fuzzy matching."""
    text = unicodedata.normalize("NFKC", str(value or "").strip().lower())
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _extract_game_entry_line(line: str) -> str | None:
    """Extract classroom-facing link text from one body line."""
    stripped = str(line or "").strip()
    if not stripped:
        return None
    match = GAME_ENTRY_LINE_PATTERN.match(stripped)
    if match:
        return f"{match.group(1)}：{match.group(2).strip()}"
    if any(marker in stripped for marker in ("互动入口", "游戏入口", "互动页面")):
        return stripped.strip("[]【】()（）")
    return None


def _extract_first_game_url(text: str) -> str | None:
    """Find one explicit game URL in raw slide text."""
    for pattern in (ABSOLUTE_URL_PATTERN, UPLOADS_URL_PATTERN):
        match = pattern.search(str(text or ""))
        if match:
            return str(match.group(1)).rstrip(".,;:)]】）")
    return None


def _extract_game_index_placeholder(text: str) -> int | None:
    """Find one structured game placeholder token in raw slide text."""
    match = GAME_LINK_PLACEHOLDER_PATTERN.search(str(text or ""))
    if not match:
        return None
    return _normalize_optional_game_index(match.group(1))


def _extract_declared_game_index(payload: dict[str, Any]) -> int | None:
    """Read a structured game anchor from explicit fields or placeholder tokens."""
    explicit = _normalize_optional_game_index(payload.get("game_index"))
    if explicit is not None:
        return explicit
    for field in ("body", "link_text", "notes"):
        declared = _extract_game_index_placeholder(str(payload.get(field) or ""))
        if declared is not None:
            return declared
    return None


def _collect_game_match_texts(game: MiniGamePayload) -> tuple[str, ...]:
    """Build compact match texts from a mini-game payload."""
    parts: list[str] = []
    for item in (
        game.title,
        game.description,
        game.learning_goal,
        game.source_section,
        "\n".join(_walk_string_leaves(game.data)[:8]),
    ):
        normalized = _normalize_game_match_key(item)
        if normalized and normalized not in parts:
            parts.append(normalized)
    return tuple(parts)


def _walk_string_leaves(value: Any) -> list[str]:
    """Extract string leaves from nested JSON-like data."""
    results: list[str] = []
    queue = [value]
    while queue and len(results) < 12:
        current = queue.pop(0)
        if isinstance(current, str):
            text = current.strip()
            if text:
                results.append(text)
            continue
        if isinstance(current, dict):
            queue.extend(current.values())
            continue
        if isinstance(current, list):
            queue.extend(current)
    return results


def _extract_game_keywords(text: str) -> set[str]:
    """Extract a small set of distinguishing gameplay labels."""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    extracted: set[str] = set()
    for canonical, aliases in GAME_KEYWORD_ALIASES:
        if any(alias in normalized for alias in aliases):
            extracted.add(canonical)
    return extracted


def _looks_like_game_link_slide(payload: dict[str, Any]) -> bool:
    """Guard fuzzy matching so ordinary slides are not bound to a game by mistake."""
    combined = "\n".join(
        str(payload.get(field) or "").strip()
        for field in ("title", "body", "source_section", "link_text")
    )
    return any(marker in combined for marker in GAME_LINK_HINT_MARKERS)


def _match_slide_to_minigame(payload: dict[str, Any], games: list[MiniGamePayload]) -> MiniGamePayload | None:
    """Pick the most likely game for one slide placeholder."""
    if not games or not _looks_like_game_link_slide(payload):
        return None

    title = _normalize_game_match_key(payload.get("title"))
    body = _normalize_game_match_key(payload.get("body"))
    combined = _normalize_game_match_key(
        "\n".join(str(payload.get(field) or "") for field in ("title", "body", "source_section", "link_text"))
    )
    raw_combined = "\n".join(str(payload.get(field) or "") for field in ("title", "body", "source_section", "link_text"))
    slide_keywords = _extract_game_keywords(raw_combined)

    best_game: MiniGamePayload | None = None
    best_score = 0.0
    best_keyword_overlap = 0
    for game in games:
        texts = _collect_game_match_texts(game)
        game_keywords = _extract_game_keywords(" ".join(filter(None, (game.title, game.description, game.learning_goal))))
        overlap = len(slide_keywords & game_keywords)
        score = 0.18 * overlap
        for candidate in texts:
            if title and (title == candidate or title in candidate or candidate in title):
                score = max(score, 0.96)
            if body and len(body) >= 6 and (body == candidate or body in candidate or candidate in body):
                score = max(score, 0.93)
            if title:
                score = max(score, SequenceMatcher(None, title, candidate).ratio())
            if combined:
                score = max(score, SequenceMatcher(None, combined, candidate).ratio())
        if score > best_score:
            best_score = score
            best_keyword_overlap = overlap
            best_game = game

    if best_game is None:
        return None
    if best_score >= GAME_MATCH_SIMILARITY:
        return best_game
    if best_keyword_overlap >= 2 and best_score >= 0.34:
        return best_game
    if slide_keywords and best_score >= GAME_MATCH_TITLE_SIMILARITY:
        return best_game
    return None


def _hydrate_replace_presentation_slide_links(
    slide: Any,
    *,
    games: list[MiniGamePayload],
) -> Any:
    """Resolve mini-game placeholder lines into clickable slide link fields."""
    if isinstance(slide, Slide):
        payload = slide.model_dump()
    elif isinstance(slide, dict):
        payload = deepcopy(slide)
    else:
        return slide

    raw_body = str(payload.get("body") or "").strip()
    body_lines: list[str] = []
    extracted_game_index = _extract_declared_game_index(payload)
    extracted_link_text = str(payload.get("link_text") or "").strip() or None
    extracted_link_url = _resolve_public_game_url(str(payload.get("link_url") or "").strip() or None)

    for raw_line in raw_body.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        placeholder_index = _extract_game_index_placeholder(line)
        if placeholder_index is not None and extracted_game_index is None:
            extracted_game_index = placeholder_index
        line = GAME_LINK_PLACEHOLDER_PATTERN.sub("", line).strip()
        if not line:
            continue
        explicit_url = _extract_first_game_url(line)
        if explicit_url and not extracted_link_url:
            extracted_link_url = _resolve_public_game_url(explicit_url)
        entry_line = _extract_game_entry_line(line)
        if entry_line:
            cleaned_line = entry_line
            if not extracted_link_text:
                extracted_link_text = cleaned_line
            body_lines.append(cleaned_line)
            continue
        body_lines.append(line)

    if extracted_game_index is not None and 1 <= extracted_game_index <= len(games):
        bound_game = games[extracted_game_index - 1]
        extracted_link_url = extracted_link_url or _resolve_public_game_url(bound_game.html_url)
        if not extracted_link_text and extracted_link_url:
            extracted_link_text = "互动入口：点击打开小游戏"
    elif extracted_game_index is not None:
        extracted_game_index = None

    if not extracted_link_url:
        matched_game = _match_slide_to_minigame(
            {
                **payload,
                "body": "\n".join(body_lines),
                "link_text": extracted_link_text,
            },
            games,
        )
        if matched_game is not None:
            extracted_link_url = _resolve_public_game_url(matched_game.html_url)
            extracted_game_index = games.index(matched_game) + 1
            if not extracted_link_text:
                extracted_link_text = "互动入口：点击打开小游戏"

    if extracted_link_text and extracted_link_text not in body_lines:
        body_lines.append(extracted_link_text)

    payload["body"] = "\n".join(body_lines).strip()
    payload["game_index"] = extracted_game_index
    payload["link_text"] = extracted_link_text
    payload["link_url"] = extracted_link_url
    return payload


def _raise_if_cancelled(cancel_token: CancellationToken | None) -> None:
    """Abort before persisting changes for a disconnected request."""
    if cancel_token is not None:
        cancel_token.raise_if_cancelled()


def _save_document(
    presentation_service: PresentationService,
    plan_id: str,
    document: PresentationDocument,
    *,
    cancel_token: CancellationToken | None = None,
) -> bool:
    """Persist the updated presentation document back into the shared plan row."""
    _raise_if_cancelled(cancel_token)
    updated = presentation_service.update(
        plan_id,
        PresentationUpdate(title=document.title, content=document.model_dump()),
    )
    return updated is not None


def _coerce_requested_template(template: Any, legacy_layout: Any = None) -> str:
    """Normalize either a template id or a legacy layout name."""
    return normalize_slide_template(template, legacy_layout)


def _template_supports_image_panel(template: str) -> bool:
    """Return whether the target template renders an image panel."""
    return get_presentation_template(template).image_box is not None


def _sync_slide_after_template_change(slide: Slide) -> bool:
    """Normalize dependent slide fields after changing the template."""
    slide.layout = resolve_template_layout_name(slide.template, slide.layout)
    if slide.template == "title_subtitle":
        if not slide.subtitle and slide.body:
            slide.subtitle = slide.body
        slide.body = ""
        slide.bullet_points = []
    elif not slide.body and slide.subtitle:
        slide.body = slide.subtitle
        slide.bullet_points = [slide.subtitle]

    cleared_image_fields = False
    if not _template_supports_image_panel(slide.template):
        if slide.image_description or slide.image_url:
            cleared_image_fields = True
        slide.image_description = None
        slide.image_url = None
    return cleared_image_fields


def _truncate_text(text: str, limit: int = 120) -> str:
    """Trim a long text fragment for compact tool responses."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}..."


def _looks_like_nonfinal_slide_text(text: str) -> bool:
    """Detect process/meta copy that should not be persisted into slide fields."""
    normalized = re.sub(r"\s+", "", str(text or "").strip())
    if not normalized:
        return False
    if "http://" in normalized.lower() or "https://" in normalized.lower():
        return False
    return any(pattern.search(normalized) for pattern in NONFINAL_SLIDE_TEXT_PATTERNS)


def _find_nonfinal_slide_field_issues(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Collect slide fields that still look like process placeholders."""
    issues: list[dict[str, str]] = []
    for field_name, field_label in NONFINAL_SLIDE_FIELD_LABELS.items():
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        if not _looks_like_nonfinal_slide_text(value):
            continue
        issues.append(
            {
                "field": field_name,
                "field_label": field_label,
                "preview": _truncate_text(value.replace("\n", " "), 40),
            }
        )
    return issues


def _slide_preview(slide: Slide) -> str:
    """Build a short classroom-facing summary for one slide."""
    if slide.subtitle:
        return f"副标题：{_truncate_text(slide.subtitle, 80)}"
    body = str(slide.body or "").strip()
    if not body and slide.bullet_points:
        body = "\n".join(item for item in slide.bullet_points if item)
    if body:
        return _truncate_text(body.replace("\n", " "), 100)
    if slide.image_description:
        return f"图片：{_truncate_text(slide.image_description, 80)}"
    if slide.notes:
        return f"备注：{_truncate_text(slide.notes, 80)}"
    return "无正文摘要"


def _format_presentation_outline(document: PresentationDocument, *, max_slides: int, include_classroom_script: bool) -> str:
    """Render a deck outline for the LLM."""
    lines = [
        f"PPT 标题：{document.title or '未命名演示文稿'}",
        f"幻灯片总数：{len(document.slides)}",
    ]
    if include_classroom_script and document.classroom_script.strip():
        lines.append(f"课堂内容稿：{_truncate_text(document.classroom_script.replace(chr(10), ' '), 220)}")
    for index, slide in enumerate(document.slides[:max_slides], start=1):
        lines.append(
            f"- 第 {index} 页 | {slide.title or f'第 {index} 页'} | 模板：{slide.template} | {_slide_preview(slide)}"
        )
    if len(document.slides) > max_slides:
        lines.append(f"- 其余 {len(document.slides) - max_slides} 页已省略")
    return "\n".join(lines)


def _match_slide_indices(document: PresentationDocument, title_keyword: str) -> list[int]:
    """Find slides whose titles contain or closely resemble the given keyword."""
    keyword = title_keyword.strip().lower()
    if not keyword:
        return []
    matches: list[int] = []
    fuzzy_candidates: list[tuple[float, int]] = []
    for index, slide in enumerate(document.slides):
        title = str(slide.title or "").strip()
        lowered_title = title.lower()
        if keyword in lowered_title or _contains_exact_keyword(title, title_keyword):
            matches.append(index)
            continue
        score = max(_best_fuzzy_similarity(title_keyword, segment) for segment in _iter_fuzzy_segments(title))
        if score >= _fuzzy_similarity_threshold(title_keyword):
            fuzzy_candidates.append((score, index))
    if matches:
        return matches
    fuzzy_candidates.sort(key=lambda item: (-item[0], item[1]))
    return [index for _, index in fuzzy_candidates]


def _render_slide_detail(document: PresentationDocument, slide_index: int, *, include_neighbors: bool) -> str:
    """Render one slide detail block, optionally including adjacent slides."""
    slide = document.slides[slide_index]
    lines = [
        f"第 {slide_index + 1} 页：{slide.title or f'第 {slide_index + 1} 页'}",
        f"模板：{slide.template}",
    ]
    if slide.subtitle:
        lines.append(f"副标题：{slide.subtitle}")
    if slide.body.strip():
        lines.append(f"正文：{slide.body.strip()}")
    if slide.bullet_points:
        lines.append(f"要点：{'; '.join(item for item in slide.bullet_points if item)}")
    if slide.image_description:
        lines.append(f"图片说明：{slide.image_description}")
    if slide.image_url:
        lines.append(f"图片链接：{slide.image_url}")
    if slide.notes:
        lines.append(f"备注：{slide.notes}")
    if slide.source_section:
        lines.append(f"来源环节：{slide.source_section}")

    if include_neighbors:
        if slide_index > 0:
            previous = document.slides[slide_index - 1]
            lines.append(
                f"前一页：第 {slide_index} 页《{previous.title or f'第 {slide_index} 页'}》 | {_slide_preview(previous)}"
            )
        if slide_index + 1 < len(document.slides):
            following = document.slides[slide_index + 1]
            lines.append(
                f"后一页：第 {slide_index + 2} 页《{following.title or f'第 {slide_index + 2} 页'}》 | {_slide_preview(following)}"
            )
    return "\n".join(lines)


def _iter_slide_search_fields(slide: Slide) -> list[tuple[str, str]]:
    """Return searchable text fields for one slide."""
    return [
        ("title", str(slide.title or "").strip()),
        ("subtitle", str(slide.subtitle or "").strip()),
        ("body", str(slide.body or "").strip()),
        ("notes", str(slide.notes or "").strip()),
        ("image_description", str(slide.image_description or "").strip()),
        ("image_url", str(slide.image_url or "").strip()),
        ("source_section", str(slide.source_section or "").strip()),
    ]


def _normalize_search_text(text: str) -> str:
    """Normalize text for tolerant search matching."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith(("P", "Z", "C"))
    )


def _contains_exact_keyword(text: str, keyword: str) -> bool:
    """Check both raw and normalized substring matches."""
    lowered_text = text.lower()
    lowered_keyword = keyword.lower()
    if lowered_keyword in lowered_text:
        return True
    normalized_keyword = _normalize_search_text(keyword)
    if not normalized_keyword:
        return False
    return normalized_keyword in _normalize_search_text(text)


def _best_fuzzy_similarity(keyword: str, text: str) -> float:
    """Estimate how closely a candidate text matches the target keyword."""
    normalized_keyword = _normalize_search_text(keyword)
    normalized_text = _normalize_search_text(text)
    if not normalized_keyword or not normalized_text:
        return 0.0
    if normalized_keyword in normalized_text:
        return 1.0
    if len(normalized_text) <= len(normalized_keyword) + 2:
        return SequenceMatcher(None, normalized_keyword, normalized_text).ratio()

    best = 0.0
    keyword_length = len(normalized_keyword)
    for window_size in range(max(1, keyword_length - 1), keyword_length + 2):
        if window_size > len(normalized_text):
            continue
        for start in range(0, len(normalized_text) - window_size + 1):
            candidate = normalized_text[start : start + window_size]
            best = max(best, SequenceMatcher(None, normalized_keyword, candidate).ratio())
            if best >= 0.98:
                return best
    return best


def _fuzzy_similarity_threshold(keyword: str) -> float:
    """Use a stricter threshold for short queries to reduce false positives."""
    normalized_keyword = _normalize_search_text(keyword)
    length = len(normalized_keyword)
    if length <= 2:
        return 0.96
    if length <= 4:
        return 0.74
    return 0.72


def _iter_fuzzy_segments(text: str) -> list[str]:
    """Split long text into smaller searchable segments for fuzzy matching."""
    candidates = [text.strip()]
    stripped_parenthetical = re.sub(r"[（(【\[].*?[】)\]）]", "", text).strip()
    if stripped_parenthetical and stripped_parenthetical not in candidates:
        candidates.append(stripped_parenthetical)
    raw_segments: list[str] = []
    for candidate in candidates:
        raw_segments.extend(re.split(r"[\n。！？!?；;]+", candidate))
    segments = [segment.strip() for segment in raw_segments if segment and segment.strip()]
    unique_segments = list(dict.fromkeys(segments))
    return unique_segments[:12]


def _make_search_snippet(text: str, keyword: str, radius: int = 55) -> str:
    """Extract a short snippet around the matched keyword."""
    lowered_text = text.lower()
    lowered_keyword = keyword.lower()
    position = lowered_text.find(lowered_keyword)
    if position < 0:
        return _truncate_text(text.replace("\n", " "), radius * 2)
    start = max(position - radius, 0)
    end = min(position + len(keyword) + radius, len(text))
    return _truncate_text(text[start:end].replace("\n", " "), radius * 2 + len(keyword))


def _build_search_result(
    *,
    slide_index: int | None,
    title: str,
    field: str,
    snippet: str,
    match_type: str,
    score: float | None = None,
) -> dict[str, Any]:
    """Create a consistent search result record."""
    result = {
        "slide_index": slide_index,
        "title": title,
        "field": field,
        "snippet": snippet,
        "match_type": match_type,
    }
    if score is not None:
        result["score"] = round(score, 3)
    return result


def _search_presentation_content(
    document: PresentationDocument,
    keyword: str,
    *,
    max_matches: int,
) -> tuple[list[dict[str, Any]], str]:
    """Search slide content with exact matching first and fuzzy fallback second."""
    matches: list[dict[str, Any]] = []

    if _contains_exact_keyword(document.classroom_script, keyword):
        matches.append(
            _build_search_result(
                slide_index=None,
                title="课堂内容稿",
                field="classroom_script",
                snippet=_make_search_snippet(document.classroom_script, keyword, radius=70),
                match_type="exact",
            )
        )

    for index, slide in enumerate(document.slides):
        title = slide.title or f"第 {index + 1} 页"
        for field, value in _iter_slide_search_fields(slide):
            if not value or not _contains_exact_keyword(value, keyword):
                continue
            matches.append(
                _build_search_result(
                    slide_index=index,
                    title=title,
                    field=field,
                    snippet=_make_search_snippet(value, keyword),
                    match_type="exact",
                )
            )
            if len(matches) >= max_matches:
                return matches, "exact"

    if matches:
        return matches, "exact"

    fuzzy_matches: list[dict[str, Any]] = []
    threshold = _fuzzy_similarity_threshold(keyword)
    search_targets: list[tuple[int | None, str, str, str]] = [
        (None, "课堂内容稿", "classroom_script", document.classroom_script),
    ]
    for index, slide in enumerate(document.slides):
        slide_title = slide.title or f"第 {index + 1} 页"
        for field, value in _iter_slide_search_fields(slide):
            search_targets.append((index, slide_title, field, value))

    for slide_index, title, field, value in search_targets:
        if not value:
            continue
        best_score = 0.0
        best_segment = ""
        for segment in _iter_fuzzy_segments(value):
            score = _best_fuzzy_similarity(keyword, segment)
            if score > best_score:
                best_score = score
                best_segment = segment
        if best_score < threshold:
            continue
        fuzzy_matches.append(
            _build_search_result(
                slide_index=slide_index,
                title=title,
                field=field,
                snippet=_truncate_text(best_segment.replace("\n", " "), 130),
                match_type="fuzzy",
                score=best_score,
            )
        )

    fuzzy_matches.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            10**9 if item.get("slide_index") is None else int(item["slide_index"]),
            str(item.get("field") or ""),
        )
    )
    return fuzzy_matches[:max_matches], "fuzzy" if fuzzy_matches else "none"


def _format_presentation_search_results(keyword: str, matches: list[dict[str, Any]], *, match_mode: str) -> str:
    """Render presentation search hits for the LLM."""
    if match_mode == "fuzzy":
        header = f"未找到与“{keyword}”完全一致的内容，返回 {len(matches)} 处最相近的 PPT 内容："
    else:
        header = f"找到 {len(matches)} 处与“{keyword}”相关的 PPT 内容："
    lines = [header, ""]
    for index, item in enumerate(matches, start=1):
        slide_index = item.get("slide_index")
        page_label = "课堂内容稿" if slide_index is None else f"第 {int(slide_index) + 1} 页"
        title = str(item.get("title") or page_label).strip()
        field = str(item.get("field") or "unknown").strip()
        snippet = str(item.get("snippet") or "").strip()
        match_label = ""
        if item.get("match_type") == "fuzzy" and isinstance(item.get("score"), (int, float)):
            match_label = f" | 匹配：模糊 {float(item['score']):.2f}"
        lines.append(f"{index}. {page_label}《{title}》 | 字段：{field}{match_label}")
        if snippet:
            lines.append(f"   片段：{snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def add_slide_tool(**kwargs: Any) -> dict[str, Any]:
    """Insert a new slide after the requested position."""
    plan_id = kwargs["plan_id"]
    template = _coerce_requested_template(kwargs.get("template"), kwargs.get("layout"))
    title = kwargs["title"].strip()
    subtitle = kwargs.get("subtitle")
    body = str(kwargs.get("body", "")).strip()
    image_description = kwargs.get("image_description")
    after_slide_index = kwargs.get("after_slide_index")
    before_slide_index = kwargs.get("before_slide_index")
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")
    nonfinal_issues = _find_nonfinal_slide_field_issues(
        {
            "title": title,
            "subtitle": subtitle,
            "body": body,
            "image_description": image_description,
        }
    )
    if nonfinal_issues:
        first_issue = nonfinal_issues[0]
        return {
            "ok": False,
            "rejected_fields": [item["field"] for item in nonfinal_issues],
            "message": (
                f"错误：{first_issue['field_label']}仍像过程性占位文案“{first_issue['preview']}”，"
                "不会写入 PPT。请先提供最终展示内容。"
            ),
        }

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if after_slide_index is not None and before_slide_index is not None:
            return {"ok": False, "message": "错误：after_slide_index 和 before_slide_index 只能提供一个。"}
        if before_slide_index is not None and (before_slide_index < 0 or before_slide_index > len(document.slides)):
            return {"ok": False, "message": f"错误：幻灯片索引 {before_slide_index} 超出范围。"}
        if after_slide_index is not None and after_slide_index != -1 and (
            after_slide_index < 0 or after_slide_index >= len(document.slides)
        ):
            return {"ok": False, "message": f"错误：幻灯片索引 {after_slide_index} 超出范围。"}

        if before_slide_index is not None:
            insert_at = before_slide_index
        elif after_slide_index is None or after_slide_index == -1:
            insert_at = len(document.slides)
        else:
            insert_at = after_slide_index + 1
        document.slides.insert(
            insert_at,
            Slide(
                template=template,
                title=title,
                subtitle=subtitle,
                body=body,
                image_description=image_description,
            ),
        )
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已添加幻灯片：{title}。",
            "slide_index": insert_at,
            "title": title,
            "subtitle": subtitle,
            "template": template,
        }
        return _record_success(op_service, conversation_id, "add_slide", kwargs, result)


def set_bullet_points_tool(**kwargs: Any) -> dict[str, Any]:
    """Replace the bullet list on a target slide."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    points = kwargs.get("points", [])
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        document.slides[slide_index].bullet_points = [point.strip() for point in points if point.strip()]
        document.slides[slide_index].body = "\n".join(document.slides[slide_index].bullet_points)
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已更新第 {slide_index + 1} 页的要点。",
            "slide_index": slide_index,
            "bullet_points": document.slides[slide_index].bullet_points,
        }
        return _record_success(op_service, conversation_id, "set_bullet_points", kwargs, result)


def change_layout_tool(**kwargs: Any) -> dict[str, Any]:
    """Change the logical layout of a target slide."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    new_layout = kwargs["new_layout"].strip()
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        slide = document.slides[slide_index]
        slide.template = _coerce_requested_template(None, new_layout)
        cleared_image_fields = _sync_slide_after_template_change(slide)
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        message = f"已将第 {slide_index + 1} 页版式改为 {new_layout}。"
        if cleared_image_fields:
            message = f"{message[:-1]}，并移除了图片占位。"
        result = {
            "ok": True,
            "message": message,
            "slide_index": slide_index,
            "template": slide.template,
            "cleared_image_fields": cleared_image_fields,
        }
        return _record_success(op_service, conversation_id, "change_layout", kwargs, result)


def add_notes_tool(**kwargs: Any) -> dict[str, Any]:
    """Add or replace speaker notes on a slide."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    notes = kwargs["notes"].strip()
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        document.slides[slide_index].notes = notes
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已更新第 {slide_index + 1} 页备注。",
            "slide_index": slide_index,
            "notes": notes,
        }
        return _record_success(op_service, conversation_id, "add_notes", kwargs, result)


def duplicate_slide_tool(**kwargs: Any) -> dict[str, Any]:
    """Duplicate a slide and insert the copy immediately after it."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        cloned_slide = document.slides[slide_index].model_copy(deep=True)
        document.slides.insert(slide_index + 1, cloned_slide)
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已复制第 {slide_index + 1} 页幻灯片。",
            "source_slide_index": slide_index,
            "new_slide_index": slide_index + 1,
        }
        return _record_success(op_service, conversation_id, "duplicate_slide", kwargs, result)


def move_slide_tool(**kwargs: Any) -> dict[str, Any]:
    """Move one slide to a new position without regenerating the whole deck."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    new_index = kwargs["new_index"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        slide_count = len(document.slides)
        if slide_index >= slide_count:
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}
        if new_index >= slide_count:
            return {"ok": False, "message": f"错误：目标索引 {new_index} 超出范围。"}

        moved_slide = document.slides.pop(slide_index)
        document.slides.insert(new_index, moved_slide)
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        title = moved_slide.title or f"第 {new_index + 1} 页"
        result = {
            "ok": True,
            "message": f"已将幻灯片“{title}”移动到第 {new_index + 1} 页。",
            "from_slide_index": slide_index,
            "to_slide_index": new_index,
            "title": title,
        }
        return _record_success(op_service, conversation_id, "move_slide", kwargs, result)


def delete_slide_tool(**kwargs: Any) -> dict[str, Any]:
    """Delete a slide by index."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        removed_title = document.slides.pop(slide_index).title or f"第 {slide_index + 1} 页"
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已删除幻灯片：{removed_title}。",
            "slide_index": slide_index,
            "deleted_title": removed_title,
        }
        return _record_success(op_service, conversation_id, "delete_slide", kwargs, result)


def replace_presentation_tool(**kwargs: Any) -> dict[str, Any]:
    """Replace the whole presentation document in one write."""
    slides = kwargs.get("slides", [])
    if not isinstance(slides, list) or not slides:
        return {
            "ok": False,
            "message": "错误：replace_presentation 需要至少 1 页幻灯片；空 slides 会清空整份演示文稿。",
        }

    plan_id = kwargs["plan_id"]
    title = kwargs.get("title")
    classroom_script = kwargs.get("classroom_script")
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        linkable_games = _load_linkable_minigames(
            plan,
            plan_service=PlanService(presentation_service.db, user_id=user_id),
        )
        if title is not None:
            document.title = str(title).strip() or document.title
        if classroom_script is not None:
            document.classroom_script = str(classroom_script).strip()
        hydrated_slides = [
            _hydrate_replace_presentation_slide_links(slide, games=linkable_games)
            for slide in slides
        ]
        document.slides = [
            slide if isinstance(slide, Slide) else Slide.model_validate(slide)
            for slide in hydrated_slides
        ]
        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        result = {
            "ok": True,
            "message": f"已整体替换演示文稿，共 {len(document.slides)} 页。",
            "title": document.title,
            "slides_count": len(document.slides),
        }
        return _record_success(op_service, conversation_id, "replace_presentation", kwargs, result)


def update_slide_content_tool(**kwargs: Any) -> dict[str, Any]:
    """Update a slide's core classroom-facing fields."""
    plan_id = kwargs["plan_id"]
    slide_index = kwargs["slide_index"]
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")
    cancel_token = kwargs.get("cancel_token")
    nonfinal_issues = _find_nonfinal_slide_field_issues(
        {
            "title": kwargs.get("title"),
            "subtitle": kwargs.get("subtitle"),
            "body": kwargs.get("body"),
            "notes": kwargs.get("notes"),
            "image_description": kwargs.get("image_description"),
        }
    )
    if nonfinal_issues:
        first_issue = nonfinal_issues[0]
        return {
            "ok": False,
            "rejected_fields": [item["field"] for item in nonfinal_issues],
            "message": (
                f"错误：{first_issue['field_label']}仍像过程性占位文案“{first_issue['preview']}”，"
                "不会写入 PPT。请先提供最终展示内容。"
            ),
        }

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        slide = document.slides[slide_index]
        template_changed = False
        if kwargs.get("title") is not None:
            slide.title = str(kwargs["title"]).strip()
        if kwargs.get("subtitle") is not None:
            slide.subtitle = str(kwargs["subtitle"]).strip() or None
        if kwargs.get("body") is not None:
            slide.body = str(kwargs["body"]).strip()
            slide.bullet_points = [line for line in slide.body.splitlines() if line.strip()]
        if kwargs.get("game_index") is not None:
            slide.game_index = _normalize_optional_game_index(kwargs.get("game_index"))
        if kwargs.get("template") is not None or kwargs.get("layout") is not None:
            slide.template = _coerce_requested_template(kwargs.get("template"), kwargs.get("layout"))
            template_changed = True
        if kwargs.get("image_description") is not None:
            slide.image_description = str(kwargs["image_description"]).strip() or None
        if kwargs.get("image_url") is not None:
            slide.image_url = str(kwargs["image_url"]).strip() or None
        if kwargs.get("notes") is not None:
            slide.notes = str(kwargs["notes"]).strip() or None
        if kwargs.get("source_section") is not None:
            slide.source_section = str(kwargs["source_section"]).strip() or None
        cleared_image_fields = _sync_slide_after_template_change(slide) if template_changed else False
        linkable_games = _load_linkable_minigames(
            plan,
            plan_service=PlanService(presentation_service.db, user_id=user_id),
        )
        hydrated_slide = _hydrate_replace_presentation_slide_links(slide, games=linkable_games)
        slide = hydrated_slide if isinstance(hydrated_slide, Slide) else Slide.model_validate(hydrated_slide)
        document.slides[slide_index] = slide

        if not _save_document(presentation_service, plan_id, document, cancel_token=cancel_token):
            return {"ok": False, "message": "错误：演示文稿更新失败。"}

        message = f"已更新第 {slide_index + 1} 页内容。"
        if cleared_image_fields:
            message = f"{message[:-1]}，并移除了图片占位。"
        result = {
            "ok": True,
            "message": message,
            "slide_index": slide_index,
            "template": slide.template,
            "title": slide.title,
            "subtitle": slide.subtitle,
            "game_index": slide.game_index,
            "cleared_image_fields": cleared_image_fields,
        }
        return _record_success(op_service, conversation_id, "update_slide_content", kwargs, result)


def get_presentation_outline_tool(**kwargs: Any) -> dict[str, Any]:
    """Read a compact outline of the current presentation."""
    plan_id = kwargs["plan_id"]
    max_slides = kwargs.get("max_slides", 12)
    include_classroom_script = kwargs.get("include_classroom_script", False)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        slides = [
            {
                "slide_index": index,
                "title": slide.title,
                "subtitle": slide.subtitle,
                "template": slide.template,
                "preview": _slide_preview(slide),
            }
            for index, slide in enumerate(document.slides[:max_slides])
        ]
        result = {
            "ok": True,
            "title": document.title,
            "slides_count": len(document.slides),
            "slides": slides,
            "message": _format_presentation_outline(
                document,
                max_slides=max_slides,
                include_classroom_script=include_classroom_script,
            ),
        }
        return _record_success(op_service, conversation_id, "get_presentation_outline", kwargs, result)


def get_slide_details_tool(**kwargs: Any) -> dict[str, Any]:
    """Read one slide and its nearby context."""
    plan_id = kwargs["plan_id"]
    requested_index = kwargs.get("slide_index")
    title_keyword = str(kwargs.get("title_keyword") or "").strip()
    include_neighbors = kwargs.get("include_neighbors", True)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        if requested_index is None:
            candidate_indices = _match_slide_indices(document, title_keyword)
            if not candidate_indices:
                return {"ok": False, "message": f"错误：没有找到标题包含“{title_keyword}”的幻灯片。"}
            if len(candidate_indices) > 1:
                choices = [
                    {"slide_index": index, "title": document.slides[index].title or f"第 {index + 1} 页"}
                    for index in candidate_indices[:6]
                ]
                return {
                    "ok": False,
                    "matches": choices,
                    "message": (
                        f"标题关键词“{title_keyword}”命中多页幻灯片，"
                        "请改用 slide_index，或提供更具体的标题关键词。"
                    ),
                }
            slide_index = candidate_indices[0]
        else:
            slide_index = requested_index

        if slide_index < 0 or slide_index >= len(document.slides):
            return {"ok": False, "message": f"错误：幻灯片索引 {slide_index} 超出范围。"}

        slide = document.slides[slide_index]
        result = {
            "ok": True,
            "slide": {
                "slide_index": slide_index,
                "title": slide.title,
                "template": slide.template,
                "subtitle": slide.subtitle,
                "body": slide.body,
                "bullet_points": slide.bullet_points,
                "image_description": slide.image_description,
                "image_url": slide.image_url,
                "notes": slide.notes,
                "source_section": slide.source_section,
            },
            "message": _render_slide_detail(document, slide_index, include_neighbors=include_neighbors),
        }
        return _record_success(op_service, conversation_id, "get_slide_details", kwargs, result)


def search_in_presentation_tool(**kwargs: Any) -> dict[str, Any]:
    """Search for keywords in the classroom script and slides."""
    plan_id = kwargs["plan_id"]
    keyword = kwargs["keyword"].strip()
    max_matches = kwargs.get("max_matches", 5)
    conversation_id = kwargs.get("conversation_id")
    user_id = kwargs.get("user_id", "default")

    with _tool_services(user_id) as (presentation_service, op_service):
        plan = presentation_service.get(plan_id)
        if plan is None:
            return {"ok": False, "message": "错误：演示文稿不存在。"}

        document = _load_document(plan)
        matches, match_mode = _search_presentation_content(document, keyword, max_matches=max_matches)

        result = {
            "ok": True,
            "keyword": keyword,
            "match_mode": match_mode,
            "matches": matches,
            "message": (
                _format_presentation_search_results(keyword, matches, match_mode=match_mode)
                if matches
                else f"未在当前 PPT 中找到与“{keyword}”相关的内容。"
            ),
        }
        return _record_success(op_service, conversation_id, "search_in_presentation", kwargs, result)


PRESENTATION_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="get_presentation_outline",
        description="读取当前 PPT 的标题、页数和幻灯片概要，适合先定位全局结构再决定改哪一页。",
        args_schema=GetPresentationOutlineArgs,
        func=get_presentation_outline_tool,
    ),
    Tool(
        name="get_slide_details",
        description="读取指定幻灯片的详细内容，可按页码或标题关键词定位，并可附带前后页摘要。",
        args_schema=GetSlideDetailsArgs,
        func=get_slide_details_tool,
    ),
    Tool(
        name="search_in_presentation",
        description="在当前 PPT 的标题、正文、备注、图片说明和课堂内容稿中搜索关键词或原句片段。",
        args_schema=SearchInPresentationArgs,
        func=search_in_presentation_tool,
    ),
    Tool(
        name="add_slide",
        description="在演示文稿中新增一页幻灯片，可指定已注册模板、标题、正文和插入位置。",
        args_schema=AddSlideArgs,
        func=add_slide_tool,
    ),
    Tool(
        name="move_slide",
        description="移动某一页到新的页序位置，适合把某页移到开头、末尾或中间而不改写整套内容。",
        args_schema=MoveSlideArgs,
        func=move_slide_tool,
    ),
    Tool(
        name="replace_presentation",
        description="一次性整体替换整份 PPT 的标题、课堂内容稿和全部幻灯片，适合大改版或重渲染。",
        args_schema=ReplacePresentationArgs,
        func=replace_presentation_tool,
    ),
    Tool(
        name="update_slide_content",
        description="修改某一页的标题、正文、模板、图片占位、图片路径或备注，适合局部精修；切到非图片模板时会自动移除图片占位字段。",
        args_schema=UpdateSlideContentArgs,
        func=update_slide_content_tool,
    ),
    Tool(
        name="set_bullet_points",
        description="设置某一页幻灯片的要点列表，并同步更新该页正文文本。",
        args_schema=SetBulletPointsArgs,
        func=set_bullet_points_tool,
    ),
    Tool(
        name="change_layout",
        description="修改某一页幻灯片的逻辑版式，系统会映射到当前已注册模板之一；切到非图片版式时会自动移除图片占位。",
        args_schema=ChangeLayoutArgs,
        func=change_layout_tool,
    ),
    Tool(
        name="add_notes",
        description="为指定幻灯片添加或更新演讲者备注。",
        args_schema=AddNotesArgs,
        func=add_notes_tool,
    ),
    Tool(
        name="duplicate_slide",
        description="复制某一页幻灯片，并插入到原页之后。",
        args_schema=DuplicateSlideArgs,
        func=duplicate_slide_tool,
    ),
    Tool(
        name="delete_slide",
        description="删除指定索引的幻灯片，属于可能有破坏性的操作。",
        args_schema=DeleteSlideArgs,
        func=delete_slide_tool,
    ),
)


def register_presentation_tools(registry: ToolsRegistry) -> ToolsRegistry:
    """Register presentation editing tools into the target registry."""
    return register_tools_once(registry, (*PRESENTATION_TOOLS, *CONTROL_FLOW_TOOLS))
