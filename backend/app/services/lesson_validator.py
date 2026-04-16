"""Validation helpers for lesson-plan editing tools."""

from __future__ import annotations

from typing import Any


def validate_plan(plan_json: dict[str, Any], total_duration_limit: int = 45) -> tuple[bool, str]:
    """Validate basic lesson-plan constraints after an edit."""
    sections = plan_json.get("sections", [])
    if not isinstance(sections, list):
        return False, "教案结构无效：sections 必须为列表。"

    if not sections:
        return False, "教案至少需要保留一个章节。"

    total_duration = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        duration = section.get("duration", 0)
        if isinstance(duration, bool):
            continue
        if isinstance(duration, (int, float)):
            total_duration += int(duration)

    if total_duration > total_duration_limit + 2:
        return False, f"总时长{total_duration}分钟超出限制({total_duration_limit}分钟)。"
    return True, ""
