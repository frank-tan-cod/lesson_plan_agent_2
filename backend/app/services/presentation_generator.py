"""Generate classroom-facing presentation projects from lesson plans."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from ..core.settings import settings
from ..presentation_layouts import get_presentation_template, list_presentation_templates, normalize_slide_template_name
from ..presentation_models import body_to_bullets, paginate_slide_text
from ..presentation_style import density_generation_hint, normalize_presentation_style, resolve_density_limits
from ..schemas import (
    GeneratePresentationRequest,
    MiniGamePayload,
    PlanUpdate,
    PresentationCreate,
    PresentationContent,
    PresentationStylePayload,
    SlidePayload,
)
from .knowledge_service import KnowledgeService
from .plan_service import PlanService
from .presentation_service import PresentationService
from .reference_context import build_reference_context

logger = logging.getLogger(__name__)

MAX_PLAN_JSON_CHARS = 12000
MAX_EXTRA_CONTEXT_CHARS = 12000
MAX_DRAFT_JSON_CHARS = 18000
IMAGE_TEMPLATE_BODY_CHARS_PER_LINE = 18
IMAGE_TEMPLATE_BODY_MAX_LINES = 4
FOLLOW_UP_BODY_CHARS_PER_LINE = 24
FOLLOW_UP_BODY_MAX_LINES = 7
REFINE_TEMPERATURE = 0.2
COVER_TITLE_KEYWORDS = ("封面", "课题", "课时", "主题", "单元")
COVER_SECTION_KEYWORDS = ("封面", "课题", "导入")
CLOSING_KEYWORDS = ("结束", "谢谢", "作业", "下课")
SUMMARY_KEYWORDS = ("总结", "小结", "回顾", "归纳", "结论", "收获", "要点")
TRANSITION_KEYWORDS = ("活动", "任务", "探究", "实验", "讨论", "练习", "思考", "观察", "过渡", "环节")
PROJECTION_LINE_CHAR_LIMIT = 24
PROJECTION_IMAGE_LINE_CHAR_LIMIT = 18
TITLE_SUBTITLE_MAX_LINES = 2
TITLE_SUBTITLE_MAX_CHARS = 34
PROJECTION_SENTENCE_SPLIT_BUFFER = 8
MIN_PROJECTION_REMAINDER_CHARS = 8
GAME_DUPLICATE_TITLE_SIMILARITY = 0.62
GAME_DUPLICATE_TEXT_SIMILARITY = 0.72
GAME_KEYWORD_MARKERS = (
    "小游戏",
    "互动游戏",
    "游戏",
    "抢答",
    "对与错",
    "选一选",
    "翻翻卡",
    "翻翻乐",
    "找一找",
    "排排序",
    "连一连",
    "闯关",
)
GAME_LINK_PLACEHOLDER_PATTERN = re.compile(r"\[\[\s*(?:GAME_LINK|小游戏入口|游戏入口|互动入口)\s*[:#：]\s*(\d+)\s*\]\]", flags=re.IGNORECASE)
PROJECTION_LEAD_INS = (
    "请同学们",
    "同学们",
    "接下来我们一起",
    "接下来一起",
    "接下来",
    "让我们一起",
    "让我们",
    "通过刚才的实验我们可以发现",
    "通过实验我们可以发现",
    "通过观察可以发现",
    "观察后可以发现",
    "我们可以发现",
    "可以发现",
    "请观察",
    "试着",
    "尝试",
)


def generate_presentation_from_plan(
    plan_id: str,
    request: GeneratePresentationRequest,
    db_session: Session,
    user_id: str,
) -> str:
    """Generate a presentation project from a lesson plan and return its id."""
    plan_service = PlanService(db_session, user_id=user_id)
    knowledge_service = KnowledgeService(db_session, user_id=user_id)
    presentation_service = PresentationService(db_session, user_id=user_id)

    plan = plan_service.get(plan_id)
    if plan is None:
        raise ValueError("教案不存在。")
    if plan.doc_type != "lesson":
        raise ValueError("只能基于教案生成 PPT。")

    extra_context = build_reference_context(
        knowledge_service=knowledge_service,
        additional_file_ids=request.additional_files,
        user_id=user_id,
    )
    style = _resolve_presentation_style(
        knowledge_service=knowledge_service,
        style=request.presentation_style,
        user_id=user_id,
    )
    prompt = _build_generation_prompt(
        plan=plan,
        extra_context=extra_context,
        course_context=request.course_context,
        presentation_style=style,
    )
    client = _get_llm_client()

    try:
        draft_payload = _request_presentation_json(
            client=client,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资深教学课件设计专家。"
                        "请先把教案改写成真实课堂上给学生看的展示内容，再输出按已注册版式组织的 JSON。"
                        "不要套用任何固定的 PPT 页型流程或预设页数。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        refined_payload = _refine_generated_payload(
            client=client,
            draft_payload=draft_payload,
            presentation_style=style,
        )
        normalized_content = _normalize_generated_content(
            refined_payload,
            fallback_title=plan.title,
            presentation_style=style,
        )
        normalized_content = _append_game_slides_from_plan(normalized_content, plan)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to generate presentation from plan %s.", plan_id)
        raise RuntimeError("LLM 生成 PPT 失败。") from exc

    presentation = presentation_service.create(
        PresentationCreate(
            title=normalized_content.title,
            content=normalized_content,
            metadata={
                "source_plan_id": plan.id,
                "generated_from": "lesson_plan",
                "additional_file_ids": list(request.additional_files),
                "course_context": (request.course_context or "").strip(),
                "presentation_style": style.model_dump(exclude_none=True),
            },
        )
    )
    _link_generated_presentation_to_plan(
        plan_service=plan_service,
        plan=plan,
        presentation=presentation,
        presentation_style=style,
    )
    return presentation.id
def _resolve_presentation_style(
    *,
    knowledge_service: KnowledgeService,
    style: PresentationStylePayload | None,
    user_id: str,
) -> PresentationStylePayload:
    """Fill in branding fields such as the public logo URL."""
    normalized = normalize_presentation_style(style)
    if normalized.logo_url or not normalized.logo_file_id:
        return normalized

    record = knowledge_service.get_file(normalized.logo_file_id, user_id=user_id)
    if record is None:
        raise ValueError(f"Logo 文件不存在：{normalized.logo_file_id}")
    if record.file_type != "image":
        raise ValueError("Logo 必须选择图片类型的知识库文件。")

    return normalized.model_copy(
        update={"logo_url": f"/uploads/images/{Path(record.storage_path).name}"}
    )


def _build_generation_prompt(
    *,
    plan: Any,
    extra_context: str,
    course_context: str | None,
    presentation_style: PresentationStylePayload | None = None,
) -> str:
    """Compose the generation prompt from plan JSON and optional references."""
    raw_plan = plan.content if isinstance(plan.content, dict) else {}
    sanitized_plan = _sanitize_plan_for_presentation_prompt(raw_plan)
    plan_json = json.dumps(sanitized_plan, ensure_ascii=False, indent=2)
    if len(plan_json) > MAX_PLAN_JSON_CHARS:
        plan_json = f"{plan_json[:MAX_PLAN_JSON_CHARS]}\n...（已截断）"
    normalized_course_context = (course_context or "").strip() or "无额外课程内容。"
    available_templates = ", ".join(list_presentation_templates())
    style = normalize_presentation_style(presentation_style)
    school_hint = style.school_name or "未指定"
    logo_hint = "已提供 Logo，可在封面/页眉中预留品牌位置。" if style.logo_url else "未提供 Logo。"
    game_prompt_hint = _build_game_prompt_hint(raw_plan)

    return f"""请根据以下教案、课程内容和参考资料，生成真实课堂上给学生看的 PPT。

教案 JSON：
{plan_json}
{game_prompt_hint}

用户补充的课程内容：
{normalized_course_context}

参考资料：
{extra_context}

展示风格偏好：
- 主题风格：{style.theme}
- {density_generation_hint(style.density)}
- 学校/机构名称：{school_hint}
- 品牌元素：{logo_hint}

要求：
1. 只输出 JSON 对象，不要输出任何解释。
2. 顶层结构必须为：
{{
  "title": "PPT标题",
  "classroom_script": "根据教案流程整理后的课堂展示文本，按教学顺序写，供后续重渲染使用",
  "slides": [
    {{
      "template": "可选值之一：{available_templates}",
      "title": "页面标题",
      "subtitle": "可选副标题，适合封面或结束页，可为空字符串",
      "body": "页面正文，直接写学生会看到的文字内容，而不是教案说明",
      "image_description": "建议配图或板书示意，可为空字符串",
      "notes": "教师讲解备注，可为空字符串",
      "source_section": "对应教案环节名称",
      "game_index": "可选正整数；若本页承接小游戏入口，填 1/2/3...，程序会自动注入真实链接"
    }}
  ]
}}
3. 必须严格遵循教案流程顺序，不得跳过关键教学环节。
4. 先把教案内容和用户补充材料融合成 classroom_script；slides 必须从这个课堂内容稿中拆分出来。
5. slides 展示的是学生可见内容，不要把“教师提问方式、操作说明、设计意图、教学目标说明”直接塞进正文，除非它们本身就是给学生看的内容。
6. 只能从当前已注册模板中选择 template，当前可用模板为：{available_templates}。
7. 页面正文要口语化、展示化、清晰具体，适合投影阅读；保留学生真正需要看到的信息，不要为了“精炼”把正文压成只剩词语标签。如果一段更适合配图，请选择带图片区的模板并填写 image_description。
7a. 如果某页只需要主标题和可选副标题，例如封面或结束页，请优先使用 title_subtitle，并把副标题写入 subtitle 字段。
7b. 当某页需要截图、实验照片、流程示意或板书图时，优先使用带图片区模板，并把正文控制成 2-4 行的讲解提纲，不要一边放图一边塞长段落。
8. 幻灯片页数、是否包含封面/目录/过渡页/总结页、是否拆分页，必须完全根据当前教案内容密度、课堂节奏和用户补充材料自行决定；不要机械套用固定流程或固定页型。
9. 如果某个教学环节根本不需要单独成页，就不要硬拆；如果某个环节信息量大，也可以拆成多页。
10. 任何单页都要以“学生坐在教室后排也能看清”为标准；宁可多分页，也不要依赖小字号承载过多内容。
11. 不要输出 bullet_points、layout 等旧字段。
12. 只有当某页确实需要承接课堂小游戏时才填写 `game_index`；不要给同一个小游戏重复设置多个入口页。
13. 小游戏入口页的职责只是“承接入口”，不是把小游戏内容重新写成 PPT 正文。不要把小游戏标题、题目、选项、答案、翻卡内容、抢答内容、玩法步骤展开成普通文字页。
14. 不要手写小游戏链接，不要在 `body`、`notes`、`subtitle` 里写“互动入口：http...”“点击这里开始游戏”之类的真实 URL 或伪链接文案；程序会根据 `game_index` 自动注入标准入口。
15. 如果只需要一句过渡提示，可以写类似“完成这个互动后，说说你的判断依据。”，并同时设置 `game_index`；不要额外生成单独的“课堂小游戏”“互动游戏”“抢答环节”纯文本页。
16. 小游戏相关 slide 请按下面的模式理解：
错误示例：
{{
  "title": "课堂小游戏：浮力快答",
  "body": "第1题…… A…… B…… 点击这里开始：http://...",
  "source_section": "课堂小游戏"
}}
正确示例：
{{
  "title": "判断浮力变化",
  "body": "完成互动后，说说你为什么这样判断。",
  "source_section": "练习",
  "game_index": 1
}}"""


def _request_presentation_json(
    *,
    client: OpenAI,
    messages: list[dict[str, str]],
    temperature: float,
) -> dict[str, Any]:
    """Request one JSON object from the LLM."""
    response = client.chat.completions.create(
        model=settings.MODEL_NAME,
        response_format={"type": "json_object"},
        messages=messages,
        temperature=temperature,
        stream=False,
    )
    raw_content = response.choices[0].message.content or "{}"
    payload = json.loads(raw_content)
    if not isinstance(payload, dict):
        raise ValueError("生成结果不是 JSON 对象。")
    return payload


def _refine_generated_payload(
    *,
    client: OpenAI,
    draft_payload: dict[str, Any],
    presentation_style: PresentationStylePayload | None,
) -> dict[str, Any]:
    """Run a low-temperature refinement pass for page typing and projection wording."""
    refine_prompt = _build_refine_prompt(
        draft_payload=draft_payload,
        presentation_style=presentation_style,
    )
    try:
        refined_payload = _request_presentation_json(
            client=client,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是课堂课件优化编辑。"
                        "你只做一次短 refine：修正页型、适度收紧正文、减少分页后的短尾页，但不要破坏原有教学顺序，也不要把正文压缩成只剩零散词语。"
                    ),
                },
                {"role": "user", "content": refine_prompt},
            ],
            temperature=REFINE_TEMPERATURE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("PPT refine pass failed, fallback to initial draft: %s", exc)
        return draft_payload
    return _merge_refined_payload(base_payload=draft_payload, refined_payload=refined_payload)


def _build_refine_prompt(
    *,
    draft_payload: dict[str, Any],
    presentation_style: PresentationStylePayload | None,
) -> str:
    """Build a focused refine prompt over the initial PPT draft."""
    style = normalize_presentation_style(presentation_style)
    draft_json = json.dumps(draft_payload, ensure_ascii=False, indent=2)
    if len(draft_json) > MAX_DRAFT_JSON_CHARS:
        draft_json = f"{draft_json[:MAX_DRAFT_JSON_CHARS]}\n...（已截断）"
    diagnostics = _build_refine_diagnostics(draft_payload, style)

    return f"""请对下面这份 PPT 初稿做一次短 refine。

把 `classroom_script` 视为课堂语义底稿，`slides` 视为待收紧的展示页初稿。

当前风格约束：
- 主题风格：{style.theme}
- {density_generation_hint(style.density)}

优先检查：
{diagnostics}

规则：
1. 保持教学顺序和核心知识点，不新增教学环节。
2. 先修页型：封面/过渡/结束页优先 `title_subtitle`；带图片说明、实验照片、示意图的页优先 `title_body_image`。
3. 图片页优先压正文，不优先拆页。正文尽量收成 2-4 行讲解提纲；只有确实压不下时，才把补充说明放到后续纯文字页。
4. 非图片页优先消短尾页。若只是轻微超量，先合并相近短句、删口头填充语，并尽量合回单页；只有明显过载时再自然拆成两页以上。
5. 正文改成适合投影的短句提纲，但要保留关键事实、结论、示例和判断依据；不要为了省字把一句完整信息压成只剩几个关键词。
6. 已经合理的页尽量少改。
6a. 如果某页承接课堂小游戏入口，保留或补上 `game_index`，不要把它改写成“课堂小游戏”纯文本页，不要展开题目/玩法/卡片内容，也不要手写 URL 或链接文案。
7. 只输出 JSON 对象，不要解释。顶层结构仍然是：
{{
  "title": "PPT标题",
  "classroom_script": "课堂内容稿",
  "slides": [
    {{
      "template": "title_body 或 title_body_image 或 title_subtitle",
      "title": "页面标题",
      "subtitle": "可选副标题",
      "body": "页面正文",
      "image_description": "图片说明，可为空字符串",
      "notes": "教师备注，可为空字符串",
      "source_section": "对应教案环节名称",
      "game_index": "可选正整数；若本页承接小游戏入口则保留"
    }}
  ]
}}

小游戏 slide 的正确方向示例：
{{
  "title": "观察后做判断",
  "body": "完成互动后，用一句话说明你的依据。",
  "source_section": "练习",
  "game_index": 1
}}

PPT 初稿 JSON：
{draft_json}"""


def _merge_refined_payload(
    *,
    base_payload: dict[str, Any],
    refined_payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge the refine result conservatively so missing fields fall back to the draft."""
    merged = dict(base_payload)
    title = _coerce_text(refined_payload.get("title"))
    classroom_script = _coerce_text(refined_payload.get("classroom_script"))
    slides = refined_payload.get("slides")

    if title:
        merged["title"] = title
    if classroom_script:
        merged["classroom_script"] = classroom_script
    if isinstance(slides, list) and slides:
        merged["slides"] = slides
    return merged


def _build_template_capacity_summary(style: PresentationStylePayload) -> str:
    """Describe approximate per-template page capacity under the selected density."""
    lines: list[str] = []
    for template_name in list_presentation_templates():
        spec = get_presentation_template(template_name)
        chars_per_line, max_lines = resolve_density_limits(
            density=style.density,
            chars_per_line=spec.pagination.chars_per_line,
            max_lines=spec.pagination.max_lines,
            has_image_panel=spec.image_box is not None,
        )
        lines.append(f"- {template_name}: 约 {chars_per_line} 字/行，{max_lines} 行/页")
    return "\n".join(lines)


def _build_refine_diagnostics(
    payload: dict[str, Any],
    style: PresentationStylePayload,
) -> str:
    """Summarize the draft slides that are most likely to need a refine pass."""
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        return "- 当前没有可诊断的页面。"

    diagnostics: list[str] = []
    for index, item in enumerate(raw_slides, start=1):
        if not isinstance(item, dict):
            continue
        template = _coerce_template(item.get("template"), item.get("layout"))
        title = _coerce_text(item.get("title")) or f"第 {index} 页"
        body = _coerce_text(item.get("body")) or "\n".join(_coerce_bullet_points(item.get("bullet_points")))
        image_description = _coerce_text(item.get("image_description")) or None
        spec = get_presentation_template(template)
        chars_per_line, max_lines = resolve_density_limits(
            density=style.density,
            chars_per_line=spec.pagination.chars_per_line,
            max_lines=spec.pagination.max_lines,
            has_image_panel=spec.image_box is not None,
        )
        estimated_pages = paginate_slide_text(
            body,
            chars_per_line=chars_per_line,
            max_lines=max_lines,
        )
        visible_lines = len([line for page in estimated_pages for line in page.splitlines() if line.strip()])
        has_image_panel = spec.image_box is not None or bool(image_description)
        if image_description and template != "title_body_image":
            diagnostics.append(f"- 第 {index} 页《{title}》含图片说明，建议检查是否应改成 `title_body_image`。")
        if has_image_panel and (visible_lines > IMAGE_TEMPLATE_BODY_MAX_LINES or len(estimated_pages) > 1):
            diagnostics.append(
                f"- 第 {index} 页《{title}》是图片页，正文预计 {visible_lines} 行 / {len(estimated_pages)} 页；优先压到 2-4 行，必要时再把余量移到后续文字页。"
            )
        elif len(estimated_pages) > 1:
            tail_lines = len([line for line in estimated_pages[-1].splitlines() if line.strip()])
            if tail_lines <= max(2, max_lines // 4):
                diagnostics.append(
                    f"- 第 {index} 页《{title}》是非图片页，预计分页 {len(estimated_pages)} 页，最后一页仅 {tail_lines} 行；若只是轻微超量，优先压回单页。"
                )
    return "\n".join(diagnostics) if diagnostics else "- 当前初稿没有明显问题，重点微调页型与投影文案。"


def _normalize_generated_content(
    payload: Any,
    *,
    fallback_title: str,
    presentation_style: PresentationStylePayload | None = None,
) -> PresentationContent:
    """Validate and normalize the LLM JSON response into presentation content."""
    if not isinstance(payload, dict):
        raise ValueError("生成结果不是 JSON 对象。")

    normalized_title = _coerce_text(payload.get("title")) or f"{fallback_title} 课件"
    classroom_script = _coerce_text(payload.get("classroom_script"))
    raw_slides = payload.get("slides")
    if not isinstance(raw_slides, list):
        raise ValueError("生成结果缺少 slides 列表。")

    drafted_slides: list[SlidePayload] = []
    for index, item in enumerate(raw_slides, start=1):
        if not isinstance(item, dict):
            continue

        template = _coerce_template(item.get("template"), item.get("layout"))
        title = _coerce_text(item.get("title")) or (normalized_title if index == 1 else f"第 {index} 页")
        subtitle = _coerce_text(item.get("subtitle")) or None
        body = _coerce_text(item.get("body"))
        bullet_points = _coerce_bullet_points(item.get("bullet_points") or body)
        game_index = item.get("game_index")
        image_description = _coerce_text(item.get("image_description")) or None
        notes = _coerce_text(item.get("notes")) or None
        source_section = _coerce_text(item.get("source_section")) or None

        if template == "title_subtitle" and not subtitle and body:
            subtitle = body
            body = ""
            bullet_points = []
        if not body and bullet_points:
            body = "\n".join(bullet_points)
        if not body and index == 1 and template != "title_subtitle":
            body = normalized_title
        if not body and template != "title_subtitle":
            continue

        drafted_slides.append(
            _normalize_single_generated_slide(
                index=index,
                total_slides=len(raw_slides),
                deck_title=normalized_title,
                template=template,
                title=title,
                subtitle=subtitle,
                body=body,
                bullet_points=bullet_points,
                game_index=game_index,
                image_description=image_description,
                notes=notes,
                source_section=source_section,
            )
        )

    slides = _expand_generated_slides(drafted_slides)
    if not slides:
        raise ValueError("生成结果中没有可用的幻灯片。")

    style = normalize_presentation_style(presentation_style)
    return PresentationContent(
        title=normalized_title,
        classroom_script=classroom_script or _build_fallback_classroom_script(slides),
        slides=_rebalance_sparse_projected_slides(slides, style=style),
    )


def _link_generated_presentation_to_plan(
    *,
    plan_service: PlanService,
    plan: Any,
    presentation: Any,
    presentation_style: PresentationStylePayload,
) -> None:
    """Persist the reverse link from a lesson plan to its generated presentation projects."""
    metadata = deepcopy(plan.metadata_json) if isinstance(plan.metadata_json, dict) else {}
    linked_ids = metadata.get("generated_presentation_ids")
    if not isinstance(linked_ids, list):
        linked_ids = []

    next_id = str(presentation.id)
    deduped_ids = [str(item) for item in linked_ids if isinstance(item, str) and item.strip() and item != next_id]
    deduped_ids.append(next_id)

    metadata["generated_presentation_ids"] = deduped_ids
    metadata["latest_generated_presentation_id"] = next_id
    metadata["latest_generated_presentation_title"] = str(presentation.title)
    metadata["presentation_style"] = presentation_style.model_dump(exclude_none=True)

    plan_service.update(
        plan.id,
        PlanUpdate(metadata=metadata),
    )


def _append_game_slides_from_plan(content: PresentationContent, plan: Any) -> PresentationContent:
    """Bind generated mini-games to declared slide anchors and append leftovers."""
    raw_content = plan.content if isinstance(plan.content, dict) else {}
    raw_games = raw_content.get("games")
    if not isinstance(raw_games, list) or not raw_games:
        return content

    normalized_games: list[MiniGamePayload] = []
    for item in raw_games:
        try:
            normalized_games.append(MiniGamePayload.model_validate(item))
        except Exception:
            continue

    if not normalized_games:
        return content

    anchored_slides: list[SlidePayload] = []
    used_game_indices: set[int] = set()
    has_explicit_game_refs = any(slide.game_index for slide in content.slides)
    for slide in content.slides:
        game_index = slide.game_index
        if game_index is not None and 1 <= game_index <= len(normalized_games) and game_index not in used_game_indices:
            anchored_slides.append(_bind_game_to_slide(slide, normalized_games[game_index - 1], game_index=game_index))
            used_game_indices.add(game_index)
            continue
        if game_index is not None:
            anchored_slides.append(slide.model_copy(update={"game_index": None}))
            continue
        anchored_slides.append(slide)

    appended_slides = (
        anchored_slides
        if has_explicit_game_refs
        else _remove_probable_game_slides(anchored_slides, normalized_games)
    )
    for game_index, game in enumerate(normalized_games, start=1):
        if game_index in used_game_indices:
            continue
        game_url = _resolve_public_game_url(game.html_url)
        link_text = "互动入口：点击打开小游戏" if game_url else None
        body_lines = [
            game.description or "请打开互动页面进行课堂小游戏。",
            f"巩固目标：{game.learning_goal or game.source_section or game.title}",
        ]
        if link_text:
            body_lines.append(link_text)
        appended_slides.append(
            SlidePayload(
                template="title_body",
                title=game.title or f"课堂小游戏 {game_index}",
                body="\n".join(line for line in body_lines if line),
                game_index=game_index,
                link_text=link_text,
                link_url=game_url,
                notes=f"{game.title}\n{game_url or ''}".strip(),
                source_section="课堂小游戏",
            )
        )

    return content.model_copy(update={"slides": appended_slides})


def _bind_game_to_slide(slide: SlidePayload, game: MiniGamePayload, *, game_index: int) -> SlidePayload:
    """Deterministically inject one mini-game link into a declared slide anchor."""
    game_url = _resolve_public_game_url(game.html_url)
    link_text = slide.link_text or ("互动入口：点击打开小游戏" if game_url else None)
    body = _strip_game_link_placeholders(slide.body)
    body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    if link_text and link_text not in body_lines:
        body_lines.append(link_text)
    normalized_body = "\n".join(body_lines).strip()
    return slide.model_copy(
        update={
            "game_index": game_index,
            "body": normalized_body,
            "bullet_points": body_to_bullets(normalized_body),
            "link_text": link_text,
            "link_url": game_url,
        }
    )


def _strip_game_link_placeholders(text: str) -> str:
    """Remove structured game-link placeholders from visible slide text."""
    return GAME_LINK_PLACEHOLDER_PATTERN.sub("", _coerce_text(text)).strip()


def _sanitize_plan_for_presentation_prompt(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Remove fields that should not be turned into regular PPT body slides."""
    sanitized = deepcopy(raw_plan) if isinstance(raw_plan, dict) else {}
    sanitized.pop("games", None)
    return sanitized


def _build_game_prompt_hint(raw_plan: dict[str, Any]) -> str:
    """Explain to the model that mini-games are handled after the main PPT draft."""
    raw_games = raw_plan.get("games")
    if not isinstance(raw_games, list) or not raw_games:
        return ""

    normalized_games: list[MiniGamePayload] = []
    for item in raw_games:
        try:
            normalized_games.append(MiniGamePayload.model_validate(item))
        except Exception:
            continue

    game_lines: list[str] = []
    for game in normalized_games[:5]:
        description = game.description or game.learning_goal or "课堂互动巩固"
        game_lines.append(f"- {game.title or '课堂小游戏'}：{description}")
    if len(normalized_games) > 5:
        game_lines.append(f"- 其余 {len(normalized_games) - 5} 个小游戏也由系统自动接管入口页。")

    summary_count = len(normalized_games) if normalized_games else len(raw_games)
    summary = f"当前教案已生成 {summary_count} 个课堂小游戏"
    summary_examples = f"\n已接管的小游戏：\n" + "\n".join(game_lines) if game_lines else ""
    return (
        "\n系统补充说明："
        f"{summary}。如果课堂流程里需要在某一页插入小游戏入口，请直接在该页写 `game_index`（从 1 开始，对应小游戏顺序），"
        "程序会自动补上标准互动入口和真实链接。"
        "如果不确定放在哪一页，可以先不写，系统会把未引用的小游戏兜底追加到 PPT 末尾。"
        "不要把小游戏数据改写成普通正文幻灯片，也不要额外展开玩法、题目、翻卡内容或抢答内容；最多只保留一句过渡提示。"
        "不要手写小游戏 URL、不要写“点击这里开始游戏”之类的伪链接文案；正确做法是只设置 `game_index`。"
        "如果需要示例，可参考：`body` 只写一句课堂过渡语，`source_section` 保持原教学环节，`game_index` 填 1/2/3。"
        f"{summary_examples}"
    )


def _remove_probable_game_slides(
    slides: list[SlidePayload],
    games: list[MiniGamePayload],
) -> list[SlidePayload]:
    """Drop game-like draft slides so appended entry slides do not appear twice."""
    if not slides:
        return []

    game_titles = {_normalize_game_match_key(game.title) for game in games if _normalize_game_match_key(game.title)}
    game_sections = {
        _normalize_game_match_key(game.source_section)
        for game in games
        if _normalize_game_match_key(game.source_section)
    }
    game_profiles = [_build_game_match_profile(game) for game in games]

    filtered: list[SlidePayload] = []
    for slide in slides:
        if _is_probable_game_slide(
            slide,
            game_titles=game_titles,
            game_sections=game_sections,
            game_profiles=game_profiles,
        ):
            continue
        filtered.append(slide)
    return filtered


def _is_probable_game_slide(
    slide: SlidePayload,
    *,
    game_titles: set[str],
    game_sections: set[str],
    game_profiles: list[dict[str, Any]],
) -> bool:
    """Heuristically detect LLM-generated game slides that should be replaced."""
    title = _normalize_game_match_key(slide.title)
    body = _normalize_game_match_key(slide.body)
    source_section = _normalize_game_match_key(slide.source_section)
    combined = _normalize_game_match_key("\n".join(part for part in (slide.title, slide.body, slide.source_section) if part))

    if "/uploads/games/" in slide.body or "互动链接：" in slide.body or "互动入口：" in slide.body:
        return True
    if title.startswith("课堂小游戏"):
        return True
    if title and title in game_titles:
        return True
    if any(marker in title for marker in ("小游戏", "互动游戏")) and (
        source_section == "课堂小游戏" or source_section in game_sections
    ):
        return True
    if any(game_title and game_title in body for game_title in game_titles):
        return True
    if not _looks_like_game_slide(title=title, body=body, source_section=source_section):
        return False
    for profile in game_profiles:
        if _slide_matches_game_profile(title=title, body=body, combined=combined, profile=profile):
            return True
    return False


def _normalize_game_match_key(value: Any) -> str:
    """Normalize slide/game labels for lightweight duplicate detection."""
    normalized = _coerce_text(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)


def _build_game_match_profile(game: MiniGamePayload) -> dict[str, Any]:
    """Collect a compact text profile used for slide-vs-game duplicate checks."""
    candidates: set[str] = set()
    for text in (
        game.title,
        game.description,
        game.learning_goal,
        _collect_game_data_text(game.data),
        "\n".join(part for part in (game.title, game.description, game.learning_goal) if part),
    ):
        normalized = _normalize_game_match_key(text)
        if normalized:
            candidates.add(normalized)
    return {"texts": tuple(candidates)}


def _collect_game_data_text(data: dict[str, Any]) -> str:
    """Flatten a small amount of game data text for similarity matching."""
    texts: list[str] = []
    for item in _walk_game_data_strings(data):
        normalized = _coerce_text(item)
        if normalized:
            texts.append(normalized)
        if len(texts) >= 8:
            break
    return "\n".join(texts)


def _walk_game_data_strings(value: Any) -> list[str]:
    """Extract string leaves from the nested game payload."""
    results: list[str] = []
    stack = [value]
    while stack and len(results) < 12:
        current = stack.pop(0)
        if isinstance(current, str):
            if current.strip():
                results.append(current.strip())
            continue
        if isinstance(current, dict):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
    return results


def _looks_like_game_slide(*, title: str, body: str, source_section: str) -> bool:
    """Gate similarity matching so ordinary teaching slides are not over-filtered."""
    combined = "\n".join(part for part in (title, body, source_section) if part)
    return any(marker in combined for marker in GAME_KEYWORD_MARKERS)


def _slide_matches_game_profile(
    *,
    title: str,
    body: str,
    combined: str,
    profile: dict[str, Any],
) -> bool:
    """Match a slide against one generated mini-game using fuzzy text overlap."""
    texts = tuple(item for item in profile.get("texts", ()) if isinstance(item, str) and item)
    if not texts:
        return False
    for candidate in texts:
        if title and (title == candidate or title in candidate or candidate in title):
            return True
        if body and len(body) >= 8 and (body == candidate or body in candidate or candidate in body):
            return True
        if title and _text_similarity(title, candidate) >= GAME_DUPLICATE_TITLE_SIMILARITY:
            return True
        if combined and _text_similarity(combined, candidate) >= GAME_DUPLICATE_TEXT_SIMILARITY:
            return True
    return False


def _text_similarity(left: str, right: str) -> float:
    """Compute a lightweight fuzzy similarity for normalized game text."""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _resolve_public_game_url(value: Any) -> str | None:
    """Upgrade stored relative game URLs to the configured public base URL."""
    raw_value = _coerce_text(value)
    if not raw_value:
        return None
    if raw_value.startswith(("http://", "https://")):
        return raw_value
    if raw_value.startswith("/"):
        return f"{settings.PUBLIC_BASE_URL}{raw_value}"
    if raw_value.startswith("uploads/"):
        return f"{settings.PUBLIC_BASE_URL}/{raw_value}"
    return raw_value


def _coerce_template(template: Any, legacy_layout: Any) -> str:
    return normalize_slide_template_name(
        _coerce_text(template),
        _coerce_text(legacy_layout),
    )


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _coerce_bullet_points(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_coerce_text(item) for item in value if _coerce_text(item)]
    if isinstance(value, str) and value.strip():
        lines = [line.strip(" -•\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return []


def _normalize_single_generated_slide(
    *,
    index: int,
    total_slides: int,
    deck_title: str,
    template: str,
    title: str,
    subtitle: str | None,
    body: str,
    bullet_points: list[str],
    game_index: Any,
    image_description: str | None,
    notes: str | None,
    source_section: str | None,
) -> SlidePayload:
    """Apply conservative template repairs before building the slide payload."""
    normalized_template = template
    normalized_subtitle = subtitle
    normalized_body = _projectionize_slide_body(
        body,
        max_chars_per_line=PROJECTION_IMAGE_LINE_CHAR_LIMIT if image_description else PROJECTION_LINE_CHAR_LIMIT,
    )
    normalized_bullets = _coerce_bullet_points(normalized_body) or list(bullet_points)
    slide_role = _infer_slide_role(
        index=index,
        total_slides=total_slides,
        deck_title=deck_title,
        title=title,
        source_section=source_section,
        body=normalized_body,
        template=normalized_template,
        image_description=image_description,
    )
    looks_like_title_page = (
        slide_role in {"cover", "transition", "closing"}
        and not image_description
        and _body_is_title_subtitle_friendly(normalized_body)
    )

    if looks_like_title_page and normalized_body and not normalized_subtitle:
        normalized_template = "title_subtitle"
        normalized_subtitle = normalized_body
        normalized_body = ""
        normalized_bullets = []
    elif image_description and normalized_template != "title_subtitle":
        normalized_template = "title_body_image"
    elif slide_role == "summary" and normalized_template == "title_subtitle" and not normalized_subtitle and normalized_body:
        normalized_subtitle = normalized_body
        normalized_body = ""
        normalized_bullets = []

    return SlidePayload(
        template=normalized_template,
        title=title,
        subtitle=normalized_subtitle,
        body=normalized_body,
        bullet_points=normalized_bullets,
        game_index=game_index,
        image_description=image_description,
        notes=notes,
        source_section=source_section,
    )


def _expand_generated_slides(slides: list[SlidePayload]) -> list[SlidePayload]:
    """Post-process generated slides so image-heavy pages stay readable."""
    expanded: list[SlidePayload] = []
    for slide in slides:
        expanded.extend(_split_image_heavy_slide(slide))
    return expanded


def _split_image_heavy_slide(slide: SlidePayload) -> list[SlidePayload]:
    """Keep the first image slide visually light and move overflow into follow-up pages."""
    if slide.template != "title_body_image":
        return [slide]

    body = _coerce_text(slide.body)
    if not body:
        return [slide]

    image_pages = paginate_slide_text(
        body,
        chars_per_line=IMAGE_TEMPLATE_BODY_CHARS_PER_LINE,
        max_lines=IMAGE_TEMPLATE_BODY_MAX_LINES,
    )
    if len(image_pages) <= 1:
        return [slide]

    first_page = slide.model_copy(
        update={
            "body": image_pages[0],
            "bullet_points": _coerce_bullet_points(image_pages[0]),
        }
    )

    remaining_body = "\n".join(image_pages[1:]).strip()
    follow_up_pages = paginate_slide_text(
        remaining_body,
        chars_per_line=FOLLOW_UP_BODY_CHARS_PER_LINE,
        max_lines=FOLLOW_UP_BODY_MAX_LINES,
    )

    follow_ups = [
        slide.model_copy(
            update={
                "template": "title_body",
                "title": f"{slide.title}（续）" if len(follow_up_pages) == 1 else f"{slide.title}（续 {page_index + 1}/{len(follow_up_pages)}）",
                "body": page,
                "bullet_points": _coerce_bullet_points(page),
                "game_index": None,
                "link_text": None,
                "link_url": None,
                "image_description": None,
            }
        )
        for page_index, page in enumerate(follow_up_pages)
        if page.strip()
    ]

    return [first_page, *follow_ups] if follow_ups else [first_page]


def _rebalance_sparse_projected_slides(
    slides: list[SlidePayload],
    *,
    style: PresentationStylePayload,
) -> list[SlidePayload]:
    """Lightly condense slides that would otherwise create a very short overflow page."""
    adjusted: list[SlidePayload] = []
    for slide in slides:
        if slide.template == "title_subtitle":
            adjusted.append(slide)
            continue

        spec = get_presentation_template(slide.template)
        chars_per_line, max_lines = resolve_density_limits(
            density=style.density,
            chars_per_line=spec.pagination.chars_per_line,
            max_lines=spec.pagination.max_lines,
            has_image_panel=spec.image_box is not None,
        )
        pages = paginate_slide_text(slide.body, chars_per_line=chars_per_line, max_lines=max_lines)
        if len(pages) <= 1:
            adjusted.append(slide)
            continue

        tail_lines = len([line for line in pages[-1].splitlines() if line.strip()])
        if tail_lines > max(2, max_lines // 4):
            adjusted.append(slide)
            continue

        merged_body = _merge_short_projection_lines(
            slide.body,
            max_chars_per_line=max(chars_per_line, 1),
        )
        merged_pages = paginate_slide_text(merged_body, chars_per_line=chars_per_line, max_lines=max_lines)
        merged_tail_lines = len([line for line in merged_pages[-1].splitlines() if line.strip()])
        if len(merged_pages) < len(pages) or (
            len(merged_pages) == len(pages) and merged_tail_lines > tail_lines
        ):
            adjusted.append(
                slide.model_copy(
                    update={
                        "body": merged_body,
                        "bullet_points": _coerce_bullet_points(merged_body),
                    }
                )
            )
            continue

        condensed_body = _projectionize_slide_body(
            slide.body,
            max_chars_per_line=max(chars_per_line + 2, 16),
        )
        condensed_pages = paginate_slide_text(condensed_body, chars_per_line=chars_per_line, max_lines=max_lines)
        if len(condensed_pages) < len(pages) or (
            len(condensed_pages) == len(pages)
            and len([line for line in condensed_pages[-1].splitlines() if line.strip()]) > tail_lines
        ):
            adjusted.append(
                slide.model_copy(
                    update={
                        "body": condensed_body,
                        "bullet_points": _coerce_bullet_points(condensed_body),
                    }
                )
            )
            continue

        adjusted.append(slide)
    return adjusted


def _merge_short_projection_lines(body: str, *, max_chars_per_line: int) -> str:
    """Pack sparse short lines into fuller projection lines before forcing a page split."""
    raw_lines = str(body or "").splitlines()
    if len(raw_lines) < 2:
        return _coerce_text(body)

    merged: list[str] = []
    current = ""
    for raw_line in raw_lines:
        line = _coerce_text(raw_line)
        if not line:
            if current:
                merged.append(current)
                current = ""
            continue
        if not current:
            current = line
            continue

        joiner = "，" if len(current) < max(max_chars_per_line // 2, 8) else "；"
        candidate = f"{current.rstrip('，、；; ')}{joiner}{line.lstrip('，、；; ')}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue

        merged.append(current)
        current = line

    if current:
        merged.append(current)

    packed = "\n".join(item for item in merged if item.strip()).strip()
    return packed or _coerce_text(body)


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Return whether a title contains one of the known title-page hints."""
    normalized = _coerce_text(text)
    return any(keyword in normalized for keyword in keywords)


def _infer_slide_role(
    *,
    index: int,
    total_slides: int,
    deck_title: str,
    title: str,
    source_section: str | None,
    body: str,
    template: str,
    image_description: str | None,
) -> str:
    """Infer whether a slide behaves like a cover, transition, summary, or closing page."""
    if image_description:
        return "content"

    title_hint = _coerce_text(title)
    section_hint = _coerce_text(source_section)
    body_hint = _coerce_text(body)
    concise_body = _body_is_title_subtitle_friendly(body_hint)
    summary_hint = _contains_any_keyword(title_hint, SUMMARY_KEYWORDS) or _contains_any_keyword(section_hint, SUMMARY_KEYWORDS)
    closing_hint = _contains_any_keyword(title_hint, CLOSING_KEYWORDS) or _contains_any_keyword(section_hint, CLOSING_KEYWORDS)
    transition_hint = _contains_any_keyword(title_hint, TRANSITION_KEYWORDS) or _contains_any_keyword(
        section_hint,
        TRANSITION_KEYWORDS,
    )
    cover_hint = (
        template == "title_subtitle"
        or title_hint == _coerce_text(deck_title)
        or _contains_any_keyword(title_hint, COVER_TITLE_KEYWORDS)
        or _contains_any_keyword(section_hint, COVER_SECTION_KEYWORDS)
    )

    if total_slides > 1 and index == 1 and concise_body and cover_hint and not summary_hint:
        return "cover"
    if total_slides > 1 and index == total_slides and concise_body and (closing_hint or summary_hint or template == "title_subtitle"):
        return "closing"
    if total_slides > 2 and 1 < index < total_slides and concise_body and transition_hint and not summary_hint:
        return "transition"
    if summary_hint:
        return "summary"
    return "content"


def _body_is_title_subtitle_friendly(body: str) -> bool:
    """Return whether body text is concise enough for a title/subtitle slide."""
    lines = [line for line in _coerce_bullet_points(body) if line]
    if not lines:
        return False
    if len(lines) > TITLE_SUBTITLE_MAX_LINES:
        return False
    return len("".join(lines)) <= TITLE_SUBTITLE_MAX_CHARS


def _projectionize_slide_body(body: str, *, max_chars_per_line: int) -> str:
    """Rewrite long prose into shorter classroom-facing projection lines."""
    normalized = _coerce_text(body)
    if not normalized:
        return ""

    collected: list[str] = []
    for paragraph in normalized.splitlines():
        candidate = _coerce_text(paragraph)
        if not candidate:
            continue
        collected.extend(_projectionize_paragraph(candidate, max_chars_per_line=max_chars_per_line))

    deduped: list[str] = []
    for line in collected:
        if not line:
            continue
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)

    return "\n".join(deduped) if deduped else normalized


def _projectionize_paragraph(paragraph: str, *, max_chars_per_line: int) -> list[str]:
    """Split one prose paragraph into concise projection-friendly fragments."""
    text = _coerce_text(paragraph)
    if not text:
        return []

    sentence_candidates: list[str] = []
    split_text = re.sub(r"(?<=[。！？；;])\s*", "\n", text)
    split_text = re.sub(r"\s*[•·●]\s*", "\n", split_text)
    split_text = re.sub(r"\s*(?:^|(?<=\s)|(?<=\n))[(（]?\d+[)）.、]\s*", "\n", split_text)
    for sentence in split_text.splitlines():
        sentence = _clean_projection_fragment(sentence)
        if not sentence:
            continue
        sentence_candidates.extend(_split_projection_sentence(sentence, max_chars_per_line=max_chars_per_line))

    projected: list[str] = []
    for sentence in sentence_candidates:
        for wrapped in paginate_slide_text(
            sentence,
            chars_per_line=max(max_chars_per_line, 1),
            max_lines=1,
        ):
            cleaned = _clean_projection_fragment(wrapped)
            if cleaned:
                projected.append(cleaned)
    return projected


def _split_projection_sentence(sentence: str, *, max_chars_per_line: int) -> list[str]:
    """Prefer semantic pauses before falling back to hard wrapping."""
    cleaned = _clean_projection_fragment(sentence)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars_per_line:
        return [cleaned]
    if len(cleaned) <= max_chars_per_line + max(PROJECTION_SENTENCE_SPLIT_BUFFER, max_chars_per_line // 3):
        return [cleaned]

    pieces = re.split(r"[，、：,:]\s*", cleaned)
    if len(pieces) <= 1:
        return [cleaned]

    segments: list[str] = []
    current = ""
    for piece in pieces:
        part = _clean_projection_fragment(piece)
        if not part:
            continue
        candidate = part if not current else f"{current}，{part}"
        if current and len(candidate) > max_chars_per_line:
            segments.append(current)
            current = part
        else:
            current = candidate

    if current:
        segments.append(current)

    return segments or [cleaned]


def _clean_projection_fragment(fragment: str) -> str:
    """Remove numbering, filler lead-ins, and trailing punctuation noise."""
    cleaned = _coerce_text(fragment)
    if not cleaned:
        return ""

    cleaned = re.sub(r"^[\-•·●]+\s*", "", cleaned)
    cleaned = re.sub(r"^[（(]?\d+[)）.、]\s*", "", cleaned)
    for prefix in PROJECTION_LEAD_INS:
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            remainder = cleaned[len(prefix) :].strip(" ，,:：")
            if len(remainder) >= MIN_PROJECTION_REMAINDER_CHARS:
                cleaned = remainder
                break
    cleaned = cleaned.strip("。；;，、 ")
    return cleaned


def _build_fallback_classroom_script(slides: list[SlidePayload]) -> str:
    """Assemble a plain-text classroom script when the model omits it."""
    blocks: list[str] = []
    for slide in slides:
        title = _coerce_text(slide.title)
        body = _coerce_text(slide.body)
        if not title and not body:
            continue
        block = f"{title}\n{body}".strip()
        blocks.append(block)
    return "\n\n".join(blocks)


def _get_llm_client() -> OpenAI:
    """Create a sync OpenAI-compatible client lazily."""
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，无法生成 PPT。")
    return OpenAI(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
    )
