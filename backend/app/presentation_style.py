"""Shared presentation style defaults, theme palettes, and pagination helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import PresentationStylePayload


@dataclass(frozen=True)
class ThemePalette:
    """Resolved color palette for one presentation theme."""

    background: tuple[int, int, int]
    surface: tuple[int, int, int]
    border: tuple[int, int, int]
    header: tuple[int, int, int]
    accent: tuple[int, int, int]
    title_on_header: tuple[int, int, int]
    title_on_cover: tuple[int, int, int]
    body: tuple[int, int, int]
    subtitle: tuple[int, int, int]
    cover_background: tuple[int, int, int]


DEFAULT_PRESENTATION_STYLE = PresentationStylePayload()

THEME_LABELS: dict[str, str] = {
    "scholastic_blue": "学院蓝",
    "forest_green": "讲堂绿",
    "sunrise_orange": "晨光橙",
}

THEME_PALETTES: dict[str, ThemePalette] = {
    "scholastic_blue": ThemePalette(
        background=(247, 243, 234),
        surface=(255, 255, 255),
        border=(221, 207, 186),
        header=(24, 49, 73),
        accent=(199, 163, 100),
        title_on_header=(255, 255, 255),
        title_on_cover=(24, 49, 73),
        body=(35, 45, 58),
        subtitle=(90, 103, 118),
        cover_background=(246, 241, 231),
    ),
    "forest_green": ThemePalette(
        background=(243, 248, 241),
        surface=(255, 255, 255),
        border=(201, 221, 204),
        header=(30, 80, 67),
        accent=(180, 147, 91),
        title_on_header=(255, 255, 255),
        title_on_cover=(30, 80, 67),
        body=(34, 54, 48),
        subtitle=(88, 109, 100),
        cover_background=(237, 245, 237),
    ),
    "sunrise_orange": ThemePalette(
        background=(250, 244, 235),
        surface=(255, 255, 255),
        border=(232, 209, 185),
        header=(125, 70, 32),
        accent=(220, 140, 71),
        title_on_header=(255, 255, 255),
        title_on_cover=(125, 70, 32),
        body=(68, 49, 38),
        subtitle=(124, 97, 79),
        cover_background=(248, 239, 228),
    ),
}


def normalize_presentation_style(payload: Any) -> PresentationStylePayload:
    """Validate arbitrary metadata/request payload into a normalized style object."""
    if isinstance(payload, PresentationStylePayload):
        return payload
    if isinstance(payload, dict):
        return PresentationStylePayload.model_validate(payload)
    return DEFAULT_PRESENTATION_STYLE.model_copy()


def extract_presentation_style(metadata: Any) -> PresentationStylePayload:
    """Read the nested `presentation_style` block from plan metadata."""
    if isinstance(metadata, dict):
        return normalize_presentation_style(metadata.get("presentation_style"))
    return DEFAULT_PRESENTATION_STYLE.model_copy()


def get_theme_palette(theme_name: str | None) -> ThemePalette:
    """Resolve a palette, falling back to the default theme."""
    normalized = (theme_name or "").strip()
    return THEME_PALETTES.get(normalized, THEME_PALETTES[DEFAULT_PRESENTATION_STYLE.theme])


def resolve_density_limits(*, density: str, chars_per_line: int, max_lines: int, has_image_panel: bool) -> tuple[int, int]:
    """Adjust pagination limits to match the selected reading density."""
    normalized = (density or "").strip()
    if normalized == "comfortable":
        char_penalty = 4 if has_image_panel else 3
        line_penalty = 3 if has_image_panel else 4
        return max(chars_per_line - char_penalty, 12), max(max_lines - line_penalty, 4)
    if normalized == "compact":
        char_bonus = 2 if has_image_panel else 1
        line_bonus = 1 if has_image_panel else 2
        return chars_per_line + char_bonus, max_lines + line_bonus
    return chars_per_line, max_lines


def resolve_font_sizes(*, density: str, has_image_panel: bool) -> dict[str, int]:
    """Return a consistent font scale for the selected density."""
    normalized = (density or "").strip()
    if normalized == "compact":
        return {
            "title": 24,
            "cover_title": 28,
            "subtitle": 13,
            "body": 12 if has_image_panel else 13,
            "placeholder": 10,
            "branding": 10,
        }
    if normalized == "balanced":
        return {
            "title": 26,
            "cover_title": 30,
            "subtitle": 15,
            "body": 14 if has_image_panel else 15,
            "placeholder": 11,
            "branding": 11,
        }
    return {
        "title": 28,
        "cover_title": 32,
        "subtitle": 16,
        "body": 15 if has_image_panel else 17,
        "placeholder": 12,
        "branding": 12,
    }


def density_generation_hint(density: str) -> str:
    """Human prompt guidance for how aggressively the generator should split slides."""
    normalized = (density or "").strip()
    if normalized == "compact":
        return "内容密度偏好：compact。允许适度浓缩信息，但仍要保证投影可读，不要把长段落塞满整页。"
    if normalized == "balanced":
        return "内容密度偏好：balanced。优先保持课堂叙事顺畅，信息量和页数适中。"
    return "内容密度偏好：comfortable。宁可多拆几页，也不要缩小字号或让单页信息过满；图片页正文尽量控制在 2-4 行。"
