"""LLM-backed lesson plan draft generation from free-form requirements."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

PLAN_GENERATION_PROMPT = """你是一个教案设计助手。请根据给定信息生成一份结构化教案初稿。

输出要求：
1. 只输出 JSON 对象，不要包含任何额外解释。
2. JSON 顶层必须包含：
   - title: 教案标题
   - sections: 数组
   - metadata: 对象
3. sections 中的每个元素都必须包含：
   - type: 章节类型
   - content: 详细、可执行的教学内容
   - duration: 数字，单位分钟，非负整数
4. 如果用户未明确给出总时长，默认按 45 分钟设计。
5. 优先覆盖这些常见章节：教学目标、导入、新授、巩固练习、小结作业。
6. metadata 中至少包含 subject 和 grade；若信息缺失可留空字符串。
7. 总时长尽量不要超过要求课时。
"""


class PlanGenerationError(RuntimeError):
    """Raised when the initial lesson-plan draft cannot be generated safely."""


def build_empty_plan_content(
    title: str,
    subject: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """Return the minimal empty plan payload used as a safe fallback."""
    return {
        "title": title,
        "sections": [],
        "metadata": {
            "subject": subject or "",
            "grade": grade or "",
        },
    }


def generate_plan_from_requirements(
    title: str,
    subject: str | None,
    grade: str | None,
    requirements: str,
    *,
    extra_context: str | None = None,
    course_context: str | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Generate a structured lesson-plan draft and fail loudly on unsafe degradation."""
    if not requirements.strip():
        return build_empty_plan_content(title=title, subject=subject, grade=grade)

    try:
        client = llm_client or _get_llm_client()
        settings = _get_settings()
        response = client.chat.completions.create(
            model=settings.MODEL_NAME,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PLAN_GENERATION_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"教学要求：{requirements.strip()}\n"
                        f"教案标题：{title}\n"
                        f"学科：{subject or '未提供'}\n"
                        f"年级：{grade or '未提供'}\n"
                        f"补充课程信息：{(course_context or '').strip() or '无'}\n"
                        f"参考资料：{(extra_context or '').strip() or '无'}"
                    ),
                },
            ],
            stream=False,
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        return _normalize_generated_plan(payload, title=title, subject=subject, grade=grade)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to generate lesson plan draft from requirements.")
        raise PlanGenerationError("教案初稿生成失败，请检查模型配置或稍后重试。") from exc


def _normalize_generated_plan(
    payload: Any,
    *,
    title: str,
    subject: str | None,
    grade: str | None,
) -> dict[str, Any]:
    """Validate and normalize the raw LLM response into plan content."""
    if not isinstance(payload, dict):
        raise ValueError("Generated plan payload must be a JSON object.")

    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        raise ValueError("Generated plan payload must include a sections list.")

    sections: list[dict[str, Any]] = []
    for index, item in enumerate(raw_sections, start=1):
        if not isinstance(item, dict):
            continue

        section_type = _coerce_text(
            item.get("type")
            or item.get("title")
            or item.get("name")
            or f"章节{index}"
        )
        section_content = _coerce_text(item.get("content"))
        if not section_content:
            continue

        sections.append(
            {
                "type": section_type or f"章节{index}",
                "content": section_content,
                "duration": _coerce_duration(item.get("duration")),
            }
        )

    if not sections:
        raise ValueError("Generated plan payload does not contain any valid sections.")

    raw_metadata = payload.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata["subject"] = subject or _coerce_text(metadata.get("subject"))
    metadata["grade"] = grade or _coerce_text(metadata.get("grade"))

    normalized_title = _coerce_text(payload.get("title")) or title
    return {
        "title": normalized_title,
        "sections": sections,
        "metadata": metadata,
    }


def _coerce_text(value: Any) -> str:
    """Convert arbitrary values into a stripped string."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _coerce_duration(value: Any) -> int:
    """Normalize a section duration to a non-negative integer."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    if isinstance(value, str):
        digits = "".join(char for char in value if char.isdigit())
        if digits:
            return max(int(digits), 0)
    return 0


def _get_llm_client() -> Any:
    """Build a sync OpenAI-compatible client lazily."""
    from openai import OpenAI
    from ..core.settings import require_llm_api_key

    settings = _get_settings()
    return OpenAI(
        api_key=require_llm_api_key("教案初稿生成"),
        base_url=settings.DEEPSEEK_BASE_URL,
    )


def _get_settings():
    """Load runtime settings lazily so imports stay lightweight."""
    from ..core.settings import settings

    return settings


__all__ = [
    "build_empty_plan_content",
    "generate_plan_from_requirements",
    "PlanGenerationError",
]
