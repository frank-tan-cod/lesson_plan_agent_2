"""Template registry for classroom presentation layouts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BoxSpec:
    """Rectangular area expressed in inches on the slide canvas."""

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class PaginationSpec:
    """Approximate line-wrapping limits for one layout."""

    chars_per_line: int
    max_lines: int


@dataclass(frozen=True)
class PresentationTemplateSpec:
    """Declarative presentation template definition."""

    name: str
    pptx_layout: str
    pagination: PaginationSpec
    body_box: BoxSpec
    image_box: BoxSpec | None = None
    legacy_layouts: tuple[str, ...] = ()
    renderer_name: str = "body"


@dataclass(frozen=True)
class PresentationRenderContext:
    """Runtime rendering inputs passed to a registered layout renderer."""

    slide: Any
    slide_data: Any
    template_spec: PresentationTemplateSpec
    rgb: Any
    shape_type: Any
    inches: Any
    pt: Any
    align: Any
    anchor: Any
    render_options: Any | None = None


DEFAULT_TEMPLATE_NAME = "title_body"

_TEMPLATE_REGISTRY: dict[str, PresentationTemplateSpec] = {}
_LEGACY_LAYOUT_MAP: dict[str, str] = {}
_RENDERER_REGISTRY: dict[str, Callable[[PresentationRenderContext], None]] = {}


def register_presentation_template(spec: PresentationTemplateSpec) -> None:
    """Register or replace a presentation template spec."""
    for layout_name, template_name in tuple(_LEGACY_LAYOUT_MAP.items()):
        if template_name == spec.name:
            _LEGACY_LAYOUT_MAP.pop(layout_name, None)
    _TEMPLATE_REGISTRY[spec.name] = spec
    for layout_name in spec.legacy_layouts:
        _LEGACY_LAYOUT_MAP[layout_name] = spec.name


def list_presentation_templates() -> tuple[str, ...]:
    """Return currently registered template names."""
    return tuple(_TEMPLATE_REGISTRY.keys())


def get_presentation_template(name: str | None) -> PresentationTemplateSpec:
    """Return a template spec, falling back to the default template."""
    normalized = (name or "").strip()
    return _TEMPLATE_REGISTRY.get(normalized, _TEMPLATE_REGISTRY[DEFAULT_TEMPLATE_NAME])


def register_presentation_renderer(
    name: str,
    renderer: Callable[[PresentationRenderContext], None],
) -> None:
    """Register or replace a layout renderer."""
    _RENDERER_REGISTRY[name] = renderer


def get_presentation_renderer(name: str | None) -> Callable[[PresentationRenderContext], None]:
    """Return a renderer, falling back to the default body renderer."""
    normalized = (name or "").strip()
    return _RENDERER_REGISTRY.get(normalized, _RENDERER_REGISTRY["body"])


def normalize_slide_template_name(template: str | None, legacy_layout: str | None = None) -> str:
    """Resolve a template name from either a template id or a legacy layout name."""
    normalized_template = (template or "").strip()
    if normalized_template in _TEMPLATE_REGISTRY:
        return normalized_template
    normalized_layout = (legacy_layout or "").strip()
    return _LEGACY_LAYOUT_MAP.get(normalized_layout, DEFAULT_TEMPLATE_NAME)


def resolve_template_layout_name(template: str | None, legacy_layout: str | None = None) -> str:
    """Return the logical legacy layout name used by older clients."""
    spec = get_presentation_template(normalize_slide_template_name(template, legacy_layout))
    return spec.pptx_layout


register_presentation_template(
    PresentationTemplateSpec(
        name="title_body",
        pptx_layout="title_content",
        pagination=PaginationSpec(chars_per_line=26, max_lines=15),
        body_box=BoxSpec(left=0.8, top=2.05, width=8.4, height=4.85),
        legacy_layouts=("title", "title_content", "two_column", "conclusion"),
    )
)

register_presentation_template(
    PresentationTemplateSpec(
        name="title_body_image",
        pptx_layout="image",
        pagination=PaginationSpec(chars_per_line=19, max_lines=12),
        body_box=BoxSpec(left=0.8, top=2.05, width=4.5, height=4.65),
        image_box=BoxSpec(left=5.55, top=2.05, width=3.65, height=4.65),
        legacy_layouts=("image",),
        renderer_name="body_image",
    )
)

register_presentation_template(
    PresentationTemplateSpec(
        name="title_subtitle",
        pptx_layout="cover",
        pagination=PaginationSpec(chars_per_line=24, max_lines=4),
        body_box=BoxSpec(left=1.25, top=4.15, width=7.5, height=1.35),
        legacy_layouts=("cover", "closing"),
        renderer_name="title_subtitle",
    )
)


__all__ = [
    "BoxSpec",
    "DEFAULT_TEMPLATE_NAME",
    "PaginationSpec",
    "PresentationRenderContext",
    "PresentationTemplateSpec",
    "get_presentation_renderer",
    "get_presentation_template",
    "list_presentation_templates",
    "normalize_slide_template_name",
    "register_presentation_renderer",
    "register_presentation_template",
    "resolve_template_layout_name",
]
