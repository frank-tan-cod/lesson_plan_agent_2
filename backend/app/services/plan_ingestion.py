"""Automatic knowledge-base ingestion for lesson plans and savepoints."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import session_maker
from ..models import KnowledgeFile, Plan
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .knowledge_service import DOCUMENT_FILE_TYPE, KnowledgeService
from .plan_service import PlanService

AUTO_INGEST_SOURCE = "plan_auto_ingest"
logger = logging.getLogger(__name__)


def plan_to_markdown(plan: Plan, *, content: dict[str, Any] | None = None) -> str:
    """Render a lesson plan or presentation into readable Markdown for knowledge indexing."""
    if plan.doc_type == "presentation":
        return presentation_to_markdown(plan, content=content)

    return lesson_plan_to_markdown(plan, content=content)


def lesson_plan_to_markdown(plan: Plan, *, content: dict[str, Any] | None = None) -> str:
    """Render a lesson plan into readable Markdown for knowledge indexing."""
    resolved_content = content if content is not None else _ensure_dict(plan.content)
    metadata = _ensure_dict(plan.metadata_json)

    lines = [f"# {plan.title}", ""]

    overview_rows: list[tuple[str, str]] = []
    if plan.doc_type:
        overview_rows.append(("文档类型", plan.doc_type))
    if plan.subject:
        overview_rows.append(("学科", plan.subject))
    if plan.grade:
        overview_rows.append(("年级", plan.grade))

    for key, label in (("semester", "学期"), ("unit", "单元"), ("version", "教材版本")):
        value = metadata.get(key)
        if value:
            overview_rows.append((label, _stringify(value)))

    if overview_rows:
        lines.extend([f"**{label}**：{value}  " for label, value in overview_rows])
        lines.append("")

    sections = resolved_content.get("sections")
    if isinstance(sections, list) and sections:
        for index, section in enumerate(sections, start=1):
            lines.extend(_render_section(section, index=index))
    else:
        lines.append("## 正文")
        lines.append("")
        lines.extend(_render_value(resolved_content))

    markdown = "\n".join(lines).strip()
    return f"{markdown}\n"


def presentation_to_markdown(plan: Plan, *, content: dict[str, Any] | None = None) -> str:
    """Render a presentation project into readable Markdown for knowledge indexing."""
    resolved_content = content if content is not None else _ensure_dict(plan.content)
    metadata = _ensure_dict(plan.metadata_json)

    lines = [f"# {plan.title}", ""]

    overview_rows: list[tuple[str, str]] = [("文档类型", plan.doc_type)]
    if plan.subject:
        overview_rows.append(("学科", plan.subject))
    if plan.grade:
        overview_rows.append(("年级", plan.grade))
    if metadata.get("generated_from"):
        overview_rows.append(("生成来源", _stringify(metadata.get("generated_from"))))
    if metadata.get("source_plan_id"):
        overview_rows.append(("来源教案 ID", _stringify(metadata.get("source_plan_id"))))

    if overview_rows:
        lines.extend([f"**{label}**：{value}  " for label, value in overview_rows])
        lines.append("")

    classroom_script = _stringify(resolved_content.get("classroom_script")).strip()
    if classroom_script:
        lines.extend(["## 课堂内容稿", "", classroom_script, ""])

    slides = resolved_content.get("slides")
    if isinstance(slides, list) and slides:
        lines.append("## 幻灯片结构")
        lines.append("")
        for index, slide in enumerate(slides, start=1):
            lines.extend(_render_slide(slide, index=index))
    else:
        lines.extend(["## 幻灯片结构", "", "暂无页面内容。", ""])

    markdown = "\n".join(lines).strip()
    return f"{markdown}\n"


async def auto_ingest_plan(
    plan_id: str,
    db_session: Session,
    knowledge_service: KnowledgeService | None = None,
    *,
    user_id: str | None = None,
    content_override: dict[str, Any] | None = None,
    trigger: str = "export",
    version: str | None = None,
    version_timestamp: datetime | None = None,
) -> KnowledgeFile | None:
    """Ingest a lesson plan or presentation snapshot into the knowledge base when it changes."""
    resolved_user_id = None if user_id is None else resolve_user_id(user_id, DEFAULT_USER_ID)
    if resolved_user_id:
        plan = PlanService(db_session, user_id=resolved_user_id).get(plan_id)
    else:
        plan = db_session.execute(select(Plan).where(Plan.id == plan_id)).scalar_one_or_none()
    if plan is None:
        return None
    if resolved_user_id is None:
        resolved_user_id = resolve_user_id(plan.user_id, DEFAULT_USER_ID)

    resolved_content = _ensure_dict(content_override if content_override is not None else plan.content)
    if not _has_meaningful_content(plan, resolved_content):
        return None

    markdown_content = plan_to_markdown(plan, content=resolved_content)
    content_hash = hashlib.md5(markdown_content.encode("utf-8")).hexdigest()
    resolved_version = version or _build_plan_version(plan, content_hash=content_hash)

    if _has_ingested_version(db_session, plan_id=plan.id, user_id=resolved_user_id, version=resolved_version):
        return None

    service = knowledge_service or KnowledgeService(db_session, user_id=resolved_user_id)
    timestamp = _format_timestamp(version_timestamp or plan.updated_at)
    filename = f"{_resolve_filename_prefix(plan.doc_type)}_{_slugify_title(plan.title)}_{timestamp}.md"
    metadata = {
        "source": AUTO_INGEST_SOURCE,
        "trigger": trigger,
        "plan_id": plan.id,
        "plan_title": plan.title,
        "doc_type": plan.doc_type,
        "version": resolved_version,
        "content_hash": content_hash,
        "plan_updated_at": _isoformat(plan.updated_at),
    }

    return await service.add_document(
        resolved_user_id,
        filename,
        markdown_content.encode("utf-8"),
        metadata_json=metadata,
    )


async def auto_ingest_presentation(
    presentation_id: str,
    db_session: Session,
    knowledge_service: KnowledgeService | None = None,
    *,
    user_id: str | None = None,
    content_override: dict[str, Any] | None = None,
    trigger: str = "presentation_create",
    version: str | None = None,
    version_timestamp: datetime | None = None,
) -> KnowledgeFile | None:
    """Alias for presentation-specific call sites to clarify intent."""
    return await auto_ingest_plan(
        presentation_id,
        db_session,
        knowledge_service=knowledge_service,
        user_id=user_id,
        content_override=content_override,
        trigger=trigger,
        version=version,
        version_timestamp=version_timestamp,
    )


async def auto_ingest_plan_task(plan_id: str, user_id: str, *, trigger: str = "export") -> None:
    """Background-task entrypoint for lesson-plan knowledge ingestion."""
    with session_maker() as session:
        try:
            await auto_ingest_plan(plan_id, session, user_id=user_id, trigger=trigger)
        except Exception:  # noqa: BLE001
            logger.exception("Background lesson-plan ingestion failed for %s.", plan_id)


async def auto_ingest_presentation_task(presentation_id: str, user_id: str, *, trigger: str = "presentation_export") -> None:
    """Background-task entrypoint for presentation knowledge ingestion."""
    with session_maker() as session:
        try:
            await auto_ingest_presentation(presentation_id, session, user_id=user_id, trigger=trigger)
        except Exception:  # noqa: BLE001
            logger.exception("Background presentation ingestion failed for %s.", presentation_id)


def _render_slide(slide: Any, *, index: int) -> list[str]:
    if not isinstance(slide, dict):
        return [f"### 第 {index} 页", "", *_render_value(slide), ""]

    title = _stringify(slide.get("title") or f"第 {index} 页").strip() or f"第 {index} 页"
    lines = [f"### 第 {index} 页：{title}", ""]

    template = _stringify(slide.get("template") or slide.get("layout")).strip()
    if template:
        lines.append(f"- **版式**：{template}")
    subtitle = _stringify(slide.get("subtitle")).strip()
    if subtitle:
        lines.append(f"- **副标题**：{subtitle}")

    body = _stringify(slide.get("body")).strip()
    bullet_points = slide.get("bullet_points")
    if body:
        lines.extend(["", body])
    elif isinstance(bullet_points, list) and bullet_points:
        lines.extend(["", *_render_value(bullet_points)])

    image_description = _stringify(slide.get("image_description")).strip()
    if image_description:
        lines.extend(["", f"*配图说明：{image_description}*"])

    notes = _stringify(slide.get("notes")).strip()
    if notes:
        lines.extend(["", f"*备注：{notes}*"])

    source_section = _stringify(slide.get("source_section")).strip()
    if source_section:
        lines.extend(["", f"*对应环节：{source_section}*"])

    lines.append("")
    return lines


def _render_section(section: Any, *, index: int) -> list[str]:
    if isinstance(section, dict):
        title = _stringify(
            section.get("title") or section.get("type") or section.get("name") or section.get("heading") or f"章节 {index}"
        )
        body = section.get("content")
        if body is None:
            body = section.get("items")
        lines = [f"## {title}", ""]
        lines.extend(_render_value(body))

        duration = section.get("duration")
        if duration not in (None, ""):
            lines.extend(["", f"*时长：{_stringify(duration)} 分钟*"])
        lines.append("")
        return lines

    return [f"## 章节 {index}", "", *_render_value(section), ""]


def _render_value(value: Any) -> list[str]:
    if value is None:
        return ["暂无内容。"]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else ["暂无内容。"]
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if isinstance(item, (dict, list)):
                nested = _render_value(item)
                if lines:
                    lines.append("")
                lines.extend(nested)
                continue
            lines.append(f"- {_stringify(item)}")
        return lines or ["暂无内容。"]
    if isinstance(value, dict):
        if not value:
            return ["暂无内容。"]
        lines: list[str] = []
        for key, nested_value in value.items():
            label = _prettify_key(key)
            if isinstance(nested_value, (dict, list)):
                if lines:
                    lines.append("")
                lines.append(f"### {label}")
                lines.append("")
                lines.extend(_render_value(nested_value))
            else:
                lines.append(f"- **{label}**：{_stringify(nested_value)}")
        return lines
    return [_stringify(value)]


def _has_meaningful_content(plan: Plan, content: dict[str, Any]) -> bool:
    if plan.doc_type == "presentation":
        classroom_script = _stringify(content.get("classroom_script")).strip()
        if len(classroom_script) >= 12:
            return True

        slides = content.get("slides")
        if isinstance(slides, list):
            for slide in slides:
                if not isinstance(slide, dict):
                    continue
                for key in ("title", "subtitle", "body", "image_description", "notes"):
                    if _stringify(slide.get(key)).strip():
                        return True
        return False

    sections = content.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                for key in ("content", "items", "title", "type", "name", "heading"):
                    if _has_visible_text(section.get(key)):
                        return True
            elif _has_visible_text(section):
                return True

    return _has_visible_text(content)


def _has_ingested_version(db_session: Session, *, plan_id: str, user_id: str, version: str) -> bool:
    stmt = select(KnowledgeFile).where(
        KnowledgeFile.user_id == user_id,
        KnowledgeFile.file_type == DOCUMENT_FILE_TYPE,
    )
    records = db_session.execute(stmt).scalars().all()
    for record in records:
        metadata = _ensure_dict(record.metadata_json)
        if (
            metadata.get("source") == AUTO_INGEST_SOURCE
            and metadata.get("plan_id") == plan_id
            and metadata.get("version") == version
        ):
            return True
    return False


def _build_plan_version(plan: Plan, *, content_hash: str) -> str:
    updated_at = _isoformat(plan.updated_at) or "unknown"
    return f"plan:{updated_at}:{content_hash}"


def _format_timestamp(value: datetime | None) -> str:
    normalized = _coerce_datetime(value) or datetime.now(timezone.utc)
    return normalized.strftime("%Y%m%d%H%M%S")


def _resolve_filename_prefix(doc_type: str | None) -> str:
    if doc_type == "presentation":
        return "PPT初稿"
    return "教案"


def _isoformat(value: datetime | None) -> str | None:
    normalized = _coerce_datetime(value)
    return normalized.isoformat() if normalized else None


def _coerce_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _slugify_title(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", title).strip().strip(".")
    return cleaned or "未命名教案"


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _has_visible_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_visible_text(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_visible_text(item) for item in value)
    return bool(str(value).strip())


def _prettify_key(key: Any) -> str:
    text = _stringify(key)
    return text.replace("_", " ").strip().title() or "字段"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
