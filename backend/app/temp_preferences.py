"""Helpers for normalizing and rendering structured teaching preferences."""

from __future__ import annotations

import json
from typing import Any

from .schemas import TempPreferencesPayload

_KNOWN_KEYS = {
    "teaching_pace",
    "interaction_level",
    "detail_level",
    "language_style",
    "visual_focus",
    "other_notes",
}

_TEACHING_PACE_PROMPTS = {
    "compact": "教学推进尽量紧凑，优先保留核心信息，避免无关展开。",
    "balanced": "教学节奏保持均衡，兼顾推进速度与学生理解。",
    "thorough": "关键内容放慢讲透，适当增加过渡、解释和停顿。",
}

_INTERACTION_LEVEL_PROMPTS = {
    "lecture": "整体以教师讲授为主，互动只保留必要节点。",
    "balanced": "保持适度互动，在关键知识点加入提问或简短交流。",
    "interactive": "尽量提高互动频率，多安排提问、讨论或学生表达。",
}

_DETAIL_LEVEL_PROMPTS = {
    "summary": "内容呈现偏概览式，结论优先，避免展开过细。",
    "balanced": "内容详略保持平衡，既给结论也保留必要过程。",
    "step_by_step": "重要内容按步骤展开，不要只给结论，要说明推导或操作过程。",
}

_LANGUAGE_STYLE_PROMPTS = {
    "rigorous": "整体表达保持专业、准确、相对严谨。",
    "conversational": "整体表达更自然口语化，贴近真实课堂交流。",
    "encouraging": "整体表达带有鼓励和引导感，帮助学生建立参与信心。",
}

_VISUAL_FOCUS_PROMPTS = {
    "auto": "视觉呈现按内容需要自动判断，不强制偏向文字或图片。",
    "text_first": "默认文字信息优先，只有确实必要时再加入图片或截图。",
    "visual_first": "如内容适合展示，优先考虑图片、案例图、示意图或截图占位。",
}

_PROMPT_TO_FIELD = {
    **{value: ("teaching_pace", key) for key, value in _TEACHING_PACE_PROMPTS.items()},
    **{value: ("interaction_level", key) for key, value in _INTERACTION_LEVEL_PROMPTS.items()},
    **{value: ("detail_level", key) for key, value in _DETAIL_LEVEL_PROMPTS.items()},
    **{value: ("language_style", key) for key, value in _LANGUAGE_STYLE_PROMPTS.items()},
    **{value: ("visual_focus", key) for key, value in _VISUAL_FOCUS_PROMPTS.items()},
}


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _clean_choice(value: Any, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _render_unknown_field(key: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        rendered = value.strip()
    else:
        rendered = json.dumps(value, ensure_ascii=False)
    if not rendered:
        return None
    return f"历史补充字段 {key}: {rendered}"


def _strip_other_notes_prefix(line: str) -> str:
    return line.removeprefix("其他要求：").removeprefix("其他要求:").lstrip("- ").strip()


def normalize_temp_preferences_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize arbitrary metadata into the supported temporary-preference shape."""
    source = payload or {}

    normalized = TempPreferencesPayload.model_validate(
        {
            "teaching_pace": _clean_choice(source.get("teaching_pace"), set(_TEACHING_PACE_PROMPTS)),
            "interaction_level": _clean_choice(source.get("interaction_level"), set(_INTERACTION_LEVEL_PROMPTS)),
            "detail_level": _clean_choice(source.get("detail_level"), set(_DETAIL_LEVEL_PROMPTS)),
            "language_style": _clean_choice(source.get("language_style"), set(_LANGUAGE_STYLE_PROMPTS)),
            "visual_focus": _clean_choice(source.get("visual_focus"), set(_VISUAL_FOCUS_PROMPTS)),
            "other_notes": _clean_text(source.get("other_notes")),
        }
    ).model_dump(exclude_none=True)

    legacy_lines = [
        _render_unknown_field(key, value)
        for key, value in source.items()
        if key not in _KNOWN_KEYS
    ]
    legacy_lines = [line for line in legacy_lines if line]
    if legacy_lines:
        existing_notes = _clean_text(normalized.get("other_notes"))
        normalized["other_notes"] = "\n".join([item for item in [existing_notes, *legacy_lines] if item])

    return normalized


def build_preference_prompt_injection(payload: dict[str, Any] | None) -> str:
    """Build prompt injection text from a structured preference payload."""
    normalized = normalize_temp_preferences_payload(payload)
    lines: list[str] = []

    teaching_pace = normalized.get("teaching_pace")
    if teaching_pace in _TEACHING_PACE_PROMPTS:
        lines.append(_TEACHING_PACE_PROMPTS[teaching_pace])

    interaction_level = normalized.get("interaction_level")
    if interaction_level in _INTERACTION_LEVEL_PROMPTS:
        lines.append(_INTERACTION_LEVEL_PROMPTS[interaction_level])

    detail_level = normalized.get("detail_level")
    if detail_level in _DETAIL_LEVEL_PROMPTS:
        lines.append(_DETAIL_LEVEL_PROMPTS[detail_level])

    language_style = normalized.get("language_style")
    if language_style in _LANGUAGE_STYLE_PROMPTS:
        lines.append(_LANGUAGE_STYLE_PROMPTS[language_style])

    visual_focus = normalized.get("visual_focus")
    if visual_focus in _VISUAL_FOCUS_PROMPTS:
        lines.append(_VISUAL_FOCUS_PROMPTS[visual_focus])

    other_notes = _clean_text(normalized.get("other_notes"))
    if other_notes:
        lines.append(f"其他要求：{other_notes}")

    return "\n".join(lines)


def parse_preference_prompt_injection(prompt: str | None) -> dict[str, Any]:
    """Parse stored prompt injection text back into structured preferences."""
    if not isinstance(prompt, str) or not prompt.strip():
        return {}

    normalized: dict[str, Any] = {}
    other_notes: list[str] = []
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("其他要求：") or line.startswith("其他要求:"):
            stripped = _strip_other_notes_prefix(line)
            if stripped:
                other_notes.append(stripped)
            continue

        mapped = _PROMPT_TO_FIELD.get(line)
        if mapped is not None:
            field_name, option_value = mapped
            normalized[field_name] = option_value
            continue

        other_notes.append(line)

    if other_notes:
        normalized["other_notes"] = "\n".join(other_notes)

    return normalize_temp_preferences_payload(normalized)


def render_temp_preferences_text(payload: dict[str, Any] | None) -> str:
    """Render temporary preferences into prompt-friendly Chinese instructions."""
    normalized = normalize_temp_preferences_payload(payload)
    if not normalized:
        return "暂无会话级临时偏好。"

    lines: list[str] = []
    teaching_pace = normalized.get("teaching_pace")
    if teaching_pace in _TEACHING_PACE_PROMPTS:
        lines.append(f"- 教学节奏：{_TEACHING_PACE_PROMPTS[teaching_pace]}")

    interaction_level = normalized.get("interaction_level")
    if interaction_level in _INTERACTION_LEVEL_PROMPTS:
        lines.append(f"- 互动强度：{_INTERACTION_LEVEL_PROMPTS[interaction_level]}")

    detail_level = normalized.get("detail_level")
    if detail_level in _DETAIL_LEVEL_PROMPTS:
        lines.append(f"- 内容展开：{_DETAIL_LEVEL_PROMPTS[detail_level]}")

    language_style = normalized.get("language_style")
    if language_style in _LANGUAGE_STYLE_PROMPTS:
        lines.append(f"- 表达风格：{_LANGUAGE_STYLE_PROMPTS[language_style]}")

    visual_focus = normalized.get("visual_focus")
    if visual_focus in _VISUAL_FOCUS_PROMPTS:
        lines.append(f"- 视觉呈现：{_VISUAL_FOCUS_PROMPTS[visual_focus]}")

    other_notes = _clean_text(normalized.get("other_notes"))
    if other_notes:
        lines.append(f"- 其他要求：{other_notes}")

    return "本次会话请额外遵循以下偏好：\n" + "\n".join(lines)
