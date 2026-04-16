"""Presentation document models shared by tools and exporters."""

from __future__ import annotations

import re
import textwrap
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .presentation_layouts import get_presentation_template, normalize_slide_template_name, resolve_template_layout_name


def coerce_presentation_text(value: Any) -> str:
    """Convert arbitrary values into a stripped string."""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def coerce_optional_positive_int(value: Any) -> int | None:
    """Convert a loose value into a positive integer when possible."""
    if value is None or isinstance(value, bool):
        return None
    candidate = coerce_presentation_text(value)
    if not candidate:
        return None
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def coerce_presentation_bullet_points(value: Any) -> list[str]:
    """Convert loose bullet payloads into a normalized list of non-empty strings."""
    if isinstance(value, list):
        return [coerce_presentation_text(item) for item in value if coerce_presentation_text(item)]
    if isinstance(value, str):
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip(" -•\t")]
    return []


def normalize_slide_template(template: Any, layout: Any) -> str:
    """Map legacy layout names onto the registered template set."""
    return normalize_slide_template_name(
        coerce_presentation_text(template),
        coerce_presentation_text(layout),
    )


def body_to_bullets(body: str) -> list[str]:
    """Derive preview bullets from the classroom body text."""
    lines = [coerce_presentation_text(line) for line in body.splitlines()]
    return [line for line in lines if line]


def paginate_slide_text(text: str, *, chars_per_line: int, max_lines: int) -> list[str]:
    """Wrap slide text approximately by characters and split it into slide-sized chunks."""
    lines = _wrap_slide_lines(text, chars_per_line=max(chars_per_line, 1))

    if not lines:
        return [""]

    page_lines = _split_lines_into_pages(lines, max_lines=max(max_lines, 1))
    page_lines = _rebalance_sparse_tail_pages(page_lines, max_lines=max(max_lines, 1))
    pages = ["\n".join(page).strip() for page in page_lines if "\n".join(page).strip()]

    return [page for page in pages if page] or [str(text or "").strip()]


def _wrap_slide_lines(text: str, *, chars_per_line: int) -> list[str]:
    """Wrap raw slide text into visual lines before pagination."""
    paragraphs = [segment.strip() for segment in str(text or "").splitlines()]
    lines: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            if lines and lines[-1] != "":
                lines.append("")
            continue

        wrapped = textwrap.wrap(
            paragraph,
            width=max(chars_per_line, 1),
            break_long_words=True,
            drop_whitespace=True,
            replace_whitespace=False,
        )
        lines.extend(wrapped or [paragraph])
    return lines


def _split_lines_into_pages(lines: list[str], *, max_lines: int) -> list[list[str]]:
    """Chunk wrapped lines into sequential pages."""
    pages: list[list[str]] = []
    current: list[str] = []
    current_lines = 0
    for line in lines:
        if current and current_lines + 1 > max(max_lines, 1):
            pages.append(current)
            current = []
            current_lines = 0
        current.append(line)
        current_lines += 1

    if current:
        pages.append(current)
    return pages


def _rebalance_sparse_tail_pages(page_lines: list[list[str]], *, max_lines: int) -> list[list[str]]:
    """Avoid a final page that contains only one or two short lines."""
    if len(page_lines) < 2:
        return page_lines

    visible_tail_lines = len([line for line in page_lines[-1] if line.strip()])
    sparse_threshold = max(2, min(3, max_lines // 3))
    if visible_tail_lines > sparse_threshold:
        return page_lines

    flat_lines = [line for page in page_lines for line in page]
    if len(flat_lines) <= max_lines:
        return page_lines

    rebalanced: list[list[str]] = []
    cursor = 0
    remaining_lines = len(flat_lines)
    total_pages = len(page_lines)
    for page_index in range(total_pages):
        pages_left = total_pages - page_index
        page_size = min(max_lines, max((remaining_lines + pages_left - 1) // pages_left, 1))
        rebalanced.append(flat_lines[cursor : cursor + page_size])
        cursor += page_size
        remaining_lines -= page_size
    return rebalanced


def strip_slide_pagination_suffix(title: str) -> str:
    """Remove a trailing `(1/2)` or `（1/2）` marker from a slide title."""
    raw_title = coerce_presentation_text(title)
    if not raw_title:
        return raw_title
    stripped = re.sub(r"\s*[（(]\s*\d+\s*/\s*\d+\s*[)）]\s*$", "", raw_title).strip()
    return stripped or raw_title


class Slide(BaseModel):
    """Single slide inside a presentation document."""

    template: str = "title_body"
    layout: str = Field(default="title_content")
    title: str = Field(default="")
    subtitle: str | None = None
    body: str = Field(default="")
    bullet_points: list[str] = Field(default_factory=list)
    game_index: int | None = None
    link_text: str | None = None
    link_url: str | None = None
    image_description: str | None = None
    image_url: str | None = None
    notes: str | None = None
    source_section: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_nullable_fields(cls, value: Any) -> Any:
        """Tolerate LLM-produced nulls and legacy layout fields before validation."""
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        if payload.get("template") is None and payload.get("layout") is not None:
            payload["template"] = payload.get("layout")

        for field_name in ("template", "layout", "title", "body"):
            payload[field_name] = coerce_presentation_text(payload.get(field_name))
        for field_name in ("subtitle", "link_text", "link_url", "image_description", "image_url", "notes", "source_section"):
            if field_name in payload:
                payload[field_name] = coerce_presentation_text(payload.get(field_name)) or None
        if "bullet_points" in payload:
            payload["bullet_points"] = coerce_presentation_bullet_points(payload.get("bullet_points"))
        if "game_index" in payload:
            payload["game_index"] = coerce_optional_positive_int(payload.get("game_index"))
        return payload

    @model_validator(mode="after")
    def normalize_fields(self) -> "Slide":
        """Keep legacy and new slide fields synchronized."""
        self.template = normalize_slide_template(self.template, self.layout)
        self.layout = resolve_template_layout_name(self.template, self.layout)
        self.title = coerce_presentation_text(self.title)
        self.subtitle = coerce_presentation_text(self.subtitle) or None
        self.body = coerce_presentation_text(self.body)
        if self.template == "title_subtitle":
            if not self.subtitle and self.body:
                self.subtitle = self.body
            self.body = ""
            self.bullet_points = []
        else:
            self.bullet_points = coerce_presentation_bullet_points(self.bullet_points)
            if not self.body and self.bullet_points:
                self.body = "\n".join(self.bullet_points)
            if self.body and not self.bullet_points:
                self.bullet_points = body_to_bullets(self.body)
        self.game_index = coerce_optional_positive_int(self.game_index)
        self.link_text = coerce_presentation_text(self.link_text) or None
        self.link_url = coerce_presentation_text(self.link_url) or None
        self.image_description = coerce_presentation_text(self.image_description) or None
        self.image_url = coerce_presentation_text(self.image_url) or None
        if get_presentation_template(self.template).image_box is None:
            self.image_description = None
            self.image_url = None
        self.notes = coerce_presentation_text(self.notes) or None
        self.source_section = coerce_presentation_text(self.source_section) or None
        return self


class PresentationDocument(BaseModel):
    """Editable presentation document persisted in the shared plan table."""

    title: str
    classroom_script: str = Field(default="")
    slides: list[Slide] = Field(default_factory=list)

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Expose the JSON schema expected by the editor."""
        return cls.model_json_schema()

    def export(self) -> bytes:
        """Export the current presentation to a PPTX binary."""
        from .services.export_pptx import export_to_pptx

        return export_to_pptx(self)

    def to_pptx(self) -> bytes:
        """Alias used by the prompt specification."""
        return self.export()
