"""Presentation-specific editor built on top of the shared document editor."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from ..models import Conversation, Operation, Plan
from ..presentation_layouts import get_presentation_template, list_presentation_templates
from ..presentation_models import (
    PresentationDocument,
    Slide,
    body_to_bullets,
    paginate_slide_text,
    strip_slide_pagination_suffix,
)
from ..schemas import Task, TaskList
from ..temp_preferences import render_temp_preferences_text
from .editor_planner import PlannerPromptContext
from .editor_service import DocumentEditor, MAX_CONTEXT_SECTIONS, MAX_SECTION_CONTEXT_CHARS


STRUCTURE_CHANGING_SLIDE_TOOLS = {"add_slide", "delete_slide", "duplicate_slide", "move_slide"}
POSITIONAL_SLIDE_KEYS = {"slide_index", "after_slide_index", "before_slide_index", "new_index"}
HIGH_RISK_PPT_MODIFY_TOOLS = {"delete_slide", "replace_presentation"}
LOCAL_SCOPE_REQUEST_HINTS = (
    "这页",
    "那页",
    "这一页",
    "那一页",
    "某页",
    "单页",
    "局部",
)
STRUCTURAL_LOCAL_REQUEST_HINTS = (
    "分页",
    "拆页",
    "重排",
    "页序",
    "页面顺序",
    "幻灯片顺序",
    "移到",
    "挪到",
    "调到",
    "放到",
)
AMBIGUOUS_LOCAL_REFERENCE_HINTS = (
    "这页",
    "那页",
    "这一页",
    "那一页",
    "这张",
    "那张",
    "这一张",
    "那一张",
    "这里",
    "这部分",
    "这个位置",
)
GLOBAL_REWRITE_REQUEST_HINTS = (
    "整体",
    "整套",
    "全部",
    "所有",
    "整份",
    "全套",
    "重做",
    "重排",
    "重新生成",
    "全面改版",
    "全改",
    "跨多页",
    "多页",
    "每一页",
)
PAGINATION_REQUEST_HINTS = (
    "分页",
    "拆页",
    "拆成两页",
    "拆成多页",
    "分成两页",
    "分成多页",
    "分两页",
    "分多页",
    "多页展示",
    "一页显示不完全",
    "一页放不下",
    "显示不完全",
    "放不下",
)
CONDENSE_REQUEST_HINTS = (
    "精简",
    "简化",
    "压缩",
    "概览",
    "概述",
    "摘要",
    "只保留核心",
    "保留核心",
    "保留要点",
    "精炼",
)
KEEP_FULL_CONTENT_HINTS = (
    "完整",
    "全文",
    "不要删减",
    "完整代码",
    "完整示例",
    "完整保留",
)
COVER_SLIDE_HINTS = (
    "封面",
    "标题页",
    "扉页",
    "首页",
    "开场页",
)
REPLACE_PLACEHOLDER_FIELD_LABELS = {
    "subtitle": "副标题",
    "body": "正文",
    "notes": "备注",
}
SLIDE_WRITE_FIELD_LABELS = {
    "title": "标题",
    "subtitle": "副标题",
    "body": "正文",
    "notes": "备注",
    "image_description": "图片说明",
}
REPLACE_PLACEHOLDER_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:\.{3,}|…{2,}|……+|略)+$"),
    re.compile(r"^(?:待补充|待完善|占位|占位文案|示意稿|示意版|todo)$", flags=re.IGNORECASE),
    re.compile(r"(两句话|几句话|若干|内容|文案).{0,8}(?:\.{3,}|…{2,}|……+)"),
    re.compile(r"(待补充|待完善|占位|示意稿|示意版|TODO|自行补充|此处略去?)", flags=re.IGNORECASE),
)
NONFINAL_SLIDE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:需要|先)(?:搜索|定位|确认|查询).{0,18}(?:后|再).{0,8}(?:填充|修改|更新|写入)"),
    re.compile(r"准备将内容(?:合并|写入|更新)到第\d+页"),
    re.compile(r"删除合并后的冗余页面"),
    re.compile(r"待确认操作"),
)


class PresentationEditor(DocumentEditor):
    """Document editor configured for `presentation` projects."""

    async def _get_plan(self) -> Plan | None:
        """Load the current presentation project only."""
        plan = await super()._get_plan()
        if plan is None or plan.doc_type != "presentation":
            return None
        return plan

    def _get_initial_modify_execution_budget(
        self,
        tasks: list[Task],
        *,
        pending_follow_up: dict[str, Any] | None,
    ) -> int | None:
        """Use the shared confirmation budget and let risk-specific hooks decide when to pause."""
        return super()._get_initial_modify_execution_budget(tasks, pending_follow_up=pending_follow_up)

    def _should_pause_for_confirmation(
        self,
        task: Task,
        *,
        tool_name: str | None,
        arguments: dict[str, Any],
        remaining_tasks: list[Task],
        remaining_modify_budget: int | None,
    ) -> bool:
        """Only confirm PPT operations that can delete content or overwrite the whole deck."""
        _ = remaining_tasks
        if task.type != "modify" or remaining_modify_budget is None or remaining_modify_budget > 0:
            return False
        if tool_name not in HIGH_RISK_PPT_MODIFY_TOOLS:
            return False
        if tool_name == "replace_presentation":
            return str(arguments.get("_auto_strategy") or "").strip() != "paginate_overflow"
        return True

    def _build_task_arguments(self, task: Task) -> dict[str, Any]:
        """Normalize tool arguments and repair obvious 1-based slide references."""
        arguments = super()._build_task_arguments(task)
        return self._align_explicit_slide_indices(task, arguments)

    def _build_content_quality_context(
        self,
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Infer PPT-specific tradeoffs such as paginate vs condense."""
        context = super()._build_content_quality_context(user_message, pending_follow_up=pending_follow_up)
        current_text = str(user_message or "").strip().lower()
        request_text = str(context.get("request_text") or "")
        current_prefers_paginate = any(token in current_text for token in PAGINATION_REQUEST_HINTS)
        current_prefers_condense = any(token in current_text for token in CONDENSE_REQUEST_HINTS)
        prefers_paginate = current_prefers_paginate or (
            not current_prefers_condense and any(token in request_text for token in PAGINATION_REQUEST_HINTS)
        )
        prefers_condense = current_prefers_condense or (
            not current_prefers_paginate and any(token in request_text for token in CONDENSE_REQUEST_HINTS)
        )
        prefers_keep_full = bool(context.get("requires_complete_content")) or any(
            token in request_text for token in KEEP_FULL_CONTENT_HINTS
        )
        context.update(
            {
                "prefers_paginate": prefers_paginate,
                "prefers_condense": prefers_condense,
                "prefers_keep_full_slide_content": prefers_keep_full,
            }
        )
        return context

    async def _rewrite_task_queue(
        self,
        tasks: list[Task],
        *,
        quality_context: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Repair local structural drift before validation, then handle overflow rewrites."""
        if not tasks:
            return tasks

        plan = await self._get_plan()
        if plan is None:
            return tasks

        request_text = str((quality_context or {}).get("request_text") or "").strip().lower()
        should_try_local_scope = bool(request_text) and self._looks_like_local_scope_request(request_text)
        should_paginate = bool((quality_context or {}).get("prefers_paginate")) and not bool(
            (quality_context or {}).get("prefers_condense")
        )

        rewritten: list[Task] = []
        for task in tasks:
            candidate_tasks = [task]
            structural_tasks = self._rewrite_replace_task_for_local_structure(task, plan)
            if structural_tasks is not None:
                candidate_tasks = structural_tasks
            elif should_try_local_scope:
                local_tasks = self._rewrite_replace_task_for_local_updates(task, plan)
                if local_tasks is not None:
                    candidate_tasks = local_tasks

            for candidate in candidate_tasks:
                candidate = self._rewrite_add_cover_task_to_front(candidate, quality_context=quality_context)
                if should_paginate:
                    candidate = self._rewrite_task_for_overflow(candidate, plan)
                rewritten.append(candidate)
        return rewritten

    async def _recognize_intent(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> TaskList | None:
        """Handle deterministic guard-rail replies before falling back to the LLM."""
        resolved = self._resolve_guard_follow_up_reply(user_message, pending_follow_up)
        if resolved is not None:
            return resolved
        forced_follow_up = self._build_unresolved_local_reference_follow_up(
            plan,
            conversation,
            recent_ops,
            user_message,
            pending_follow_up=pending_follow_up,
        )
        if forced_follow_up is not None:
            return forced_follow_up
        return await super()._recognize_intent(
            plan,
            conversation,
            recent_ops,
            user_message,
            pending_follow_up=pending_follow_up,
        )

    def _resolve_guard_follow_up_reply(
        self,
        user_message: str,
        pending_follow_up: dict[str, Any] | None,
    ) -> TaskList | None:
        """Interpret the common safety follow-up replies without another LLM hop."""
        if not pending_follow_up:
            return None

        follow_up_kind = str(pending_follow_up.get("follow_up_kind") or "").strip()
        if not follow_up_kind:
            return None

        normalized_message = re.sub(r"[\s，,。.!！？?、；;：:]+", "", str(user_message or "").strip().lower())
        if not normalized_message:
            return None

        if any(token in normalized_message for token in ("取消这批修改", "取消这次修改", "取消修改", "取消")):
            return TaskList(goal_status="need_more_steps", tasks=[Task(type="cancel")])

        if self._accepts_current_result_without_more_changes(normalized_message):
            return TaskList(
                goal_status="complete",
                tasks=[
                    Task(
                        type="reply",
                        response="好的，当前这版 PPT 就按现在的结构保留，我这边不再继续修改。",
                    )
                ],
            )

        if follow_up_kind == "slide_overflow_guard":
            if any(
                token in normalized_message
                for token in ("自动分页保留完整内容", "自动分页", "分页保留完整内容", "分页", "拆成多页", "分成多页", "分两页")
            ):
                remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
                if not remaining_tasks:
                    return None
                return TaskList(goal_status="need_more_steps", tasks=remaining_tasks)
            if any(
                token in normalized_message
                for token in ("改成单页精简版", "单页精简版", "单页精简", "精简版", "精简", "压缩成一页", "保留单页")
            ):
                remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
                if not remaining_tasks:
                    return None
                plan = self.plan_service.get(self.plan_id)
                if plan is None or plan.doc_type != "presentation":
                    return TaskList(goal_status="need_more_steps", tasks=remaining_tasks)
                condensed_tasks = [self._rewrite_task_for_condense(task, plan) for task in remaining_tasks]
                return TaskList(goal_status="need_more_steps", tasks=condensed_tasks)
            return None

        if follow_up_kind == "replace_scope_guard":
            if any(token in normalized_message for token in ("整体重排", "整套重排", "整体改", "全量重排")):
                remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
                if not remaining_tasks:
                    return None
                return TaskList(goal_status="need_more_steps", tasks=remaining_tasks)
            if any(token in normalized_message for token in ("按局部修改", "局部修改", "按页修改", "逐页修改")):
                remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
                if not remaining_tasks:
                    return None
                plan = self.plan_service.get(self.plan_id)
                if plan is None or plan.doc_type != "presentation":
                    return None
                rewritten = self._rewrite_scope_guard_tasks_for_local_updates(remaining_tasks, plan)
                if rewritten is None:
                    return None
                return TaskList(goal_status="need_more_steps", tasks=rewritten)
            return None

        if follow_up_kind == "empty_replace_guard":
            if any(token in normalized_message for token in ("整体重排", "整套重排", "整体改", "全量重排")):
                return TaskList(
                    goal_status="need_follow_up",
                    tasks=[
                        Task(
                            type="follow_up",
                            parameters={
                                "question": (
                                    "要继续整体重排，我还需要完整的页级结果。"
                                    "请直接提供重排后的每页标题和正文，或至少给出完整页序与每页要点。"
                                ),
                                "options": ["我来补完整页级内容", "取消这次修改"],
                            },
                        )
                    ],
                )
            if any(token in normalized_message for token in ("按局部修改", "局部修改", "按页修改", "逐页修改")):
                previous_message = str(pending_follow_up.get("previous_user_message") or "").strip()
                question = (
                    "我可以改按局部工具处理，但当前计划里没有可直接执行的页级 payload。"
                    "请直接告诉我要删哪一页、在哪一页后新增，或哪一页要改成什么。"
                )
                if previous_message:
                    question = (
                        f"你上一轮原始需求是“{self._truncate_text(previous_message, 80)}”。"
                        + question
                    )
                return TaskList(
                    goal_status="need_follow_up",
                    tasks=[
                        Task(
                            type="follow_up",
                            parameters={
                                "question": question,
                                "options": ["补充具体页级修改", "取消这次修改"],
                            },
                        )
                    ],
                )
            return None

        if follow_up_kind == "replace_content_guard":
            if any(token in normalized_message for token in ("保留其余页面原内容", "保留原内容", "其余页面保留原内容", "其他页面保留原内容")):
                remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
                if not remaining_tasks:
                    return None
                plan = self.plan_service.get(self.plan_id)
                if plan is None or plan.doc_type != "presentation":
                    return None
                rewritten = self._rewrite_replace_content_guard_tasks(remaining_tasks, plan)
                if rewritten is None:
                    return None
                return TaskList(goal_status="need_more_steps", tasks=rewritten)
            return None

        if follow_up_kind != "slide_reorder_conflict":
            return None

        if normalized_message not in {"只做第一步", "先做第一步", "只保留第一步"}:
            return None

        remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
        if not remaining_tasks:
            return None
        return TaskList(goal_status="need_more_steps", tasks=[remaining_tasks[0]])

    def _accepts_current_result_without_more_changes(self, normalized_message: str) -> bool:
        """Detect explicit user acceptance after a safety follow-up."""
        accept_tokens = (
            "无需其他修改",
            "无需修改",
            "不需要其他修改",
            "不需要修改",
            "不用修改",
            "不用再改",
            "无其他修改",
            "就这样",
            "这样就行",
            "可以了",
            "没问题",
            "当前10页结构正是我想要的",
            "当前结构正是我想要的",
            "这个结构就是我想要的",
        )
        return any(token in normalized_message for token in accept_tokens)

    def _build_guard_follow_up_planner_hint(self, pending_follow_up: dict[str, Any] | None) -> str:
        """Inject precise resume instructions for editor-generated safety follow-ups."""
        if not pending_follow_up:
            return ""

        follow_up_kind = str(pending_follow_up.get("follow_up_kind") or "").strip()
        if follow_up_kind == "slide_reorder_conflict":
            return (
                "这是系统对“页序变更后仍沿用旧页码”的安全追问。\n"
                "- 如果用户回复“只做第一步”，只保留 remaining_tasks 里的第一个结构改动，不要再追加后续按旧页码的任务。\n"
                "- 如果用户回复“整体重排”，只有在同时要重写多页内容时才改成 `replace_presentation`；若只是调页序，优先改用 `move_slide`。\n"
                "- 如果用户回复“取消这批修改”，就返回 cancel 任务。\n\n"
            )
        if follow_up_kind in {"replace_scope_guard", "empty_replace_guard"}:
            return (
                "这是系统对 `replace_presentation` 的安全追问。\n"
                "- 如果用户回复“按局部修改”，要改用 update_slide_content / add_slide / move_slide / delete_slide / change_layout 等局部工具，不要再次直接输出 replace_presentation。\n"
                "- 若用户要新增封面且没单独指定位置，优先把它插到第一页前，使用 `add_slide.before_slide_index=0`。\n"
                "- 如果用户回复“整体重排”，可以使用 `replace_presentation`，但必须给出完整且非空的 slides。\n"
                "- 如果用户回复“取消这次修改”，就返回 cancel 任务。\n\n"
            )
        if follow_up_kind == "slide_overflow_guard":
            return (
                "这是系统对“单页内容明显放不下”的追问。\n"
                "- 如果用户回复“自动分页保留完整内容”或表达同意分页，优先保留原内容并拆成多页；若会影响页序，优先用 `replace_presentation` 一次性提交拆页后的完整结果。\n"
                "- 如果用户回复“改成单页精简版”，保留单页结构，但必须把正文压缩到当前模板可容纳的长度，不要再输出明显放不下的长代码或长段落。\n"
                "- 如果用户回复“取消这次修改”，就返回 cancel 任务。\n\n"
            )
        if follow_up_kind == "replace_content_guard":
            return (
                "这是系统对 `replace_presentation` 内容完整性的安全追问。\n"
                "- 如果用户回复“保留其余页面原内容”，未明确要求改动的页面必须沿用当前 PPT 的原内容，不要把正文写成“两句话……”或其他占位稿。\n"
                "- 如果用户回复“我会提供完整重排文案”，可以继续使用 `replace_presentation`，但每一页写回内容都必须是最终可落稿文本，不能出现省略号占位、TODO 或待补充表述。\n"
                "- 如果用户回复“取消这次修改”，就返回 cancel 任务。\n\n"
            )
        return ""

    def _get_presentation_document(self, plan: Plan) -> PresentationDocument:
        """Normalize the stored plan into a validated presentation document."""
        content = deepcopy(plan.content) if isinstance(plan.content, dict) else {}
        payload = {
            "title": content.get("title") or plan.title,
            "classroom_script": content.get("classroom_script") or "",
            "slides": content.get("slides") or [],
        }
        return PresentationDocument.model_validate(payload)

    def _merge_update_into_slide(self, base_slide: Slide, arguments: dict[str, Any]) -> Slide:
        """Apply an update_slide_content payload onto an existing slide snapshot."""
        payload = base_slide.model_dump()
        for field_name in ("title", "subtitle", "body", "template", "image_description", "image_url", "notes", "source_section"):
            if field_name in arguments and arguments.get(field_name) is not None:
                payload[field_name] = arguments.get(field_name)
        return Slide.model_validate(payload)

    def _build_slide_from_add_task(self, arguments: dict[str, Any]) -> Slide:
        """Construct a slide model from add_slide arguments."""
        return Slide.model_validate(
            {
                "template": arguments.get("template") or arguments.get("layout") or "title_body",
                "title": arguments.get("title") or "",
                "subtitle": arguments.get("subtitle"),
                "body": arguments.get("body") or "",
                "image_description": arguments.get("image_description"),
            }
        )

    def _rewrite_add_cover_task_to_front(
        self,
        task: Task,
        *,
        quality_context: dict[str, Any] | None = None,
    ) -> Task:
        """Default newly added cover/title slides to the front when no position was specified."""
        if task.type != "modify" or self._resolve_tool_name(task) != "add_slide":
            return task

        raw_parameters = task.parameters if isinstance(task.parameters, dict) else {}
        if "after_slide_index" in raw_parameters or "before_slide_index" in raw_parameters:
            return task

        request_text = str((quality_context or {}).get("request_text") or "").strip().lower()
        title = str(raw_parameters.get("title") or "").strip().lower()
        if not self._looks_like_cover_request(request_text, title=title):
            return task

        updated_task = task.model_copy(deep=True)
        updated_task.parameters = deepcopy(updated_task.parameters)
        updated_task.parameters["before_slide_index"] = 0
        return updated_task

    def _looks_like_cover_request(self, request_text: str, *, title: str = "") -> bool:
        """Infer whether the user is asking for a cover-like opening slide."""
        text = f"{request_text} {title}".strip()
        if not text:
            return False
        return any(token in text for token in COVER_SLIDE_HINTS)

    def _condense_slide_for_editing(self, slide: Slide) -> Slide:
        """Shrink one slide to fit a single page when the user explicitly chooses condense."""
        template_spec = get_presentation_template(slide.template)
        source_text = slide.subtitle if slide.template == "title_subtitle" else slide.body
        pages = paginate_slide_text(
            source_text or "",
            chars_per_line=template_spec.pagination.chars_per_line,
            max_lines=template_spec.pagination.max_lines,
        )
        if len(pages) <= 1:
            return slide

        condensed_text = pages[0]
        if slide.template == "title_subtitle":
            return slide.model_copy(update={"subtitle": condensed_text, "body": "", "bullet_points": []})
        return slide.model_copy(update={"body": condensed_text, "bullet_points": body_to_bullets(condensed_text)})

    def _rewrite_scope_guard_tasks_for_local_updates(
        self,
        tasks: list[Task],
        plan: Plan,
    ) -> list[Task] | None:
        """Convert safe local replace tasks into structural/local slide operations."""
        rewritten: list[Task] = []
        for task in tasks:
            structural_tasks = self._rewrite_replace_task_for_local_structure(task, plan)
            if structural_tasks is not None:
                rewritten.extend(structural_tasks)
                continue
            local_tasks = self._rewrite_replace_task_for_local_updates(task, plan)
            if local_tasks is None:
                return None
            rewritten.extend(local_tasks)
        return rewritten

    def _rewrite_replace_task_for_local_updates(self, task: Task, plan: Plan) -> list[Task] | None:
        """Rewrite one replace_presentation task into update_slide_content calls when safe."""
        if task.type != "modify":
            return [task]

        tool_name = self._resolve_tool_name(task)
        if tool_name != "replace_presentation":
            return [task]

        arguments = self._build_task_arguments(task)
        document = self._get_presentation_document(plan)
        slides = arguments.get("slides")
        if not isinstance(slides, list) or len(slides) != len(document.slides):
            return None
        if str(arguments.get("title") or document.title).strip() != document.title:
            return None
        if str(arguments.get("classroom_script") or document.classroom_script).strip() != document.classroom_script:
            return None

        rewritten: list[Task] = []
        for slide_index, raw_slide in enumerate(slides):
            proposed = Slide.model_validate(raw_slide)
            current = document.slides[slide_index]
            changed_fields = self._build_local_slide_update_parameters(current, proposed, raw_slide)
            if not changed_fields:
                continue
            rewritten.append(
                Task(
                    type="modify",
                    tool_name="update_slide_content",
                    action="rewrite",
                    target=proposed.title or current.title or f"第 {slide_index + 1} 页",
                    parameters={"slide_index": slide_index, **changed_fields},
                )
            )
        return rewritten

    def _build_local_slide_update_parameters(
        self,
        current: Slide,
        proposed: Slide,
        raw_slide: Any,
    ) -> dict[str, Any]:
        """Extract only explicitly provided slide fields that differ from current content."""
        changed: dict[str, Any] = {}
        raw = raw_slide if isinstance(raw_slide, dict) else {}
        explicit_template = "template" in raw or "layout" in raw
        target_template = proposed.template if explicit_template else current.template

        for field_name in ("title",):
            if field_name not in raw:
                continue
            current_value = getattr(current, field_name)
            proposed_value = getattr(proposed, field_name)
            if current_value != proposed_value:
                changed[field_name] = proposed_value

        if "subtitle" in raw:
            if current.subtitle != proposed.subtitle:
                changed["subtitle"] = proposed.subtitle or ""

        if "body" in raw:
            if target_template == "title_subtitle" and "subtitle" not in raw:
                if current.subtitle != proposed.subtitle:
                    changed["subtitle"] = proposed.subtitle or ""
            elif current.body != proposed.body:
                changed["body"] = proposed.body

        if explicit_template:
            if current.template != proposed.template:
                changed["template"] = proposed.template

        for field_name in ("image_description", "image_url", "notes", "source_section"):
            if field_name not in raw:
                continue
            current_value = getattr(current, field_name)
            proposed_value = getattr(proposed, field_name)
            if current_value != proposed_value:
                changed[field_name] = proposed_value or ""
        return changed

    def _rewrite_replace_task_for_local_structure(self, task: Task, plan: Plan) -> list[Task] | None:
        """Rewrite safe structural deck diffs into add/delete/move tasks."""
        if task.type != "modify":
            return [task]

        if self._resolve_tool_name(task) != "replace_presentation":
            return None

        arguments = self._build_task_arguments(task)
        document = self._get_presentation_document(plan)
        slides = arguments.get("slides")
        if not isinstance(slides, list):
            return None
        if str(arguments.get("title") or document.title).strip() != document.title:
            return None
        if str(arguments.get("classroom_script") or document.classroom_script).strip() != document.classroom_script:
            return None

        target_slides = [Slide.model_validate(raw_slide) for raw_slide in slides]

        insertion = self._detect_single_slide_insertion(document.slides, target_slides)
        if insertion is not None:
            insert_index, slide = insertion
            return [
                Task(
                    type="modify",
                    tool_name="add_slide",
                    action="insert",
                    target=slide.title or f"第 {insert_index + 1} 页",
                    parameters=self._build_add_slide_parameters_for_index(slide, insert_index, len(document.slides)),
                )
            ]

        deletion_index = self._detect_single_slide_deletion(document.slides, target_slides)
        if deletion_index is not None:
            return [
                Task(
                    type="modify",
                    tool_name="delete_slide",
                    action="delete",
                    target=document.slides[deletion_index].title or f"第 {deletion_index + 1} 页",
                    parameters={"slide_index": deletion_index},
                )
            ]

        move_tasks = self._build_move_slide_tasks_for_reorder(document.slides, target_slides)
        if move_tasks is not None and move_tasks:
            return move_tasks
        return None

    def _build_add_slide_parameters_for_index(
        self,
        slide: Slide,
        insert_index: int,
        current_slide_count: int,
    ) -> dict[str, Any]:
        """Encode an insertion target using before/after semantics."""
        parameters = {
            "template": slide.template,
            "title": slide.title or "",
            "body": slide.body or "",
        }
        if slide.subtitle is not None:
            parameters["subtitle"] = slide.subtitle
        if slide.image_description is not None:
            parameters["image_description"] = slide.image_description

        if insert_index <= 0:
            parameters["before_slide_index"] = 0
        elif insert_index >= current_slide_count:
            parameters["after_slide_index"] = current_slide_count - 1
        else:
            parameters["before_slide_index"] = insert_index
        return parameters

    def _detect_single_slide_insertion(
        self,
        current_slides: list[Slide],
        target_slides: list[Slide],
    ) -> tuple[int, Slide] | None:
        """Detect whether the target deck is the current deck plus exactly one new slide."""
        if len(target_slides) != len(current_slides) + 1:
            return None

        current_signatures = [self._slide_signature(slide) for slide in current_slides]
        target_signatures = [self._slide_signature(slide) for slide in target_slides]
        current_index = 0
        target_index = 0
        insertion_index: int | None = None
        inserted_slide: Slide | None = None

        while current_index < len(current_signatures) and target_index < len(target_signatures):
            if current_signatures[current_index] == target_signatures[target_index]:
                current_index += 1
                target_index += 1
                continue
            if insertion_index is not None:
                return None
            insertion_index = target_index
            inserted_slide = target_slides[target_index]
            target_index += 1

        if insertion_index is None:
            insertion_index = len(target_slides) - 1
            inserted_slide = target_slides[-1]

        if current_index != len(current_signatures):
            return None
        return insertion_index, inserted_slide

    def _detect_single_slide_deletion(
        self,
        current_slides: list[Slide],
        target_slides: list[Slide],
    ) -> int | None:
        """Detect whether the target deck is the current deck minus exactly one slide."""
        if len(target_slides) != len(current_slides) - 1:
            return None

        current_signatures = [self._slide_signature(slide) for slide in current_slides]
        target_signatures = [self._slide_signature(slide) for slide in target_slides]
        current_index = 0
        target_index = 0
        deletion_index: int | None = None

        while current_index < len(current_signatures) and target_index < len(target_signatures):
            if current_signatures[current_index] == target_signatures[target_index]:
                current_index += 1
                target_index += 1
                continue
            if deletion_index is not None:
                return None
            deletion_index = current_index
            current_index += 1

        if deletion_index is None:
            deletion_index = len(current_signatures) - 1

        if target_index != len(target_signatures):
            return None
        return deletion_index

    def _build_move_slide_tasks_for_reorder(
        self,
        current_slides: list[Slide],
        target_slides: list[Slide],
    ) -> list[Task] | None:
        """Convert a pure permutation into move_slide tasks on the live deck."""
        if len(current_slides) != len(target_slides):
            return None

        current_signatures = [self._slide_signature(slide) for slide in current_slides]
        target_signatures = [self._slide_signature(slide) for slide in target_slides]
        if Counter(current_signatures) != Counter(target_signatures):
            return None
        if current_signatures == target_signatures:
            return []

        working = [
            {"signature": signature, "title": current_slides[index].title or f"第 {index + 1} 页"}
            for index, signature in enumerate(current_signatures)
        ]
        tasks: list[Task] = []
        for target_index, target_signature in enumerate(target_signatures):
            if working[target_index]["signature"] == target_signature:
                continue
            source_index = next(
                (
                    index
                    for index in range(target_index + 1, len(working))
                    if working[index]["signature"] == target_signature
                ),
                None,
            )
            if source_index is None:
                return None
            slide_title = str(working[source_index]["title"]).strip()
            tasks.append(
                Task(
                    type="modify",
                    tool_name="move_slide",
                    action="reorder",
                    target=slide_title or f"第 {source_index + 1} 页",
                    parameters={"slide_index": source_index, "new_index": target_index},
                )
            )
            moved = working.pop(source_index)
            working.insert(target_index, moved)
        return tasks

    def _slide_signature(self, slide: Slide) -> str:
        """Create a stable content signature for structural diffing."""
        return json.dumps(slide.model_dump(), ensure_ascii=False, sort_keys=True)

    def _rewrite_replace_content_guard_tasks(
        self,
        tasks: list[Task],
        plan: Plan,
    ) -> list[Task] | None:
        """Repair replace-presentation tasks by preserving current content on untouched slides."""
        rewritten: list[Task] = []
        for task in tasks:
            repaired = self._rewrite_replace_task_to_preserve_current_content(task, plan)
            if repaired is None:
                return None
            rewritten.append(repaired)
        return rewritten

    def _rewrite_replace_task_to_preserve_current_content(self, task: Task, plan: Plan) -> Task | None:
        """Fill placeholder fields in a whole-deck rewrite from the current deck when safe."""
        if task.type != "modify":
            return task

        tool_name = self._resolve_tool_name(task)
        if tool_name != "replace_presentation":
            return task

        arguments = self._build_task_arguments(task)
        document = self._get_presentation_document(plan)
        slides = arguments.get("slides")
        if not isinstance(slides, list):
            return None

        repaired_slides: list[dict[str, Any]] = []
        for slide_index, raw_slide in enumerate(slides):
            proposed = Slide.model_validate(raw_slide)
            current = document.slides[slide_index] if slide_index < len(document.slides) else None
            repaired = self._repair_placeholder_fields_with_current_slide(proposed, current, raw_slide)
            if repaired is None:
                return None
            repaired_slides.append(repaired.model_dump())

        if len(document.slides) > len(repaired_slides):
            repaired_slides.extend(slide.model_dump() for slide in document.slides[len(repaired_slides) :])

        updated_task = task.model_copy(deep=True)
        updated_task.parameters = deepcopy(updated_task.parameters)
        updated_task.parameters["title"] = str(arguments.get("title") or document.title).strip() or document.title
        updated_task.parameters["classroom_script"] = (
            str(arguments.get("classroom_script") or document.classroom_script).strip() or document.classroom_script
        )
        updated_task.parameters["slides"] = repaired_slides
        return updated_task

    def _repair_placeholder_fields_with_current_slide(
        self,
        proposed: Slide,
        current: Slide | None,
        raw_slide: Any,
    ) -> Slide | None:
        """Replace placeholder or omitted fields with current slide content when an original slide exists."""
        raw = raw_slide if isinstance(raw_slide, dict) else {}
        updates: dict[str, Any] = {}
        explicit_template = "template" in raw or "layout" in raw
        target_template = proposed.template if explicit_template else (current.template if current is not None else proposed.template)

        for field_name in ("template", "title", "notes", "source_section"):
            explicit = field_name in raw or (field_name == "template" and ("template" in raw or "layout" in raw))
            if not explicit:
                if current is None:
                    continue
                updates[field_name] = getattr(current, field_name)
                continue
            text = str(getattr(proposed, field_name) or "").strip()
            if field_name == "template":
                if current is not None and proposed.template == current.template:
                    continue
                continue
            if not self._looks_like_replace_placeholder_text(text):
                continue
            if current is None:
                return None
            updates[field_name] = getattr(current, field_name)

        if target_template == "title_subtitle":
            content_explicit = "subtitle" in raw or "body" in raw
            if not content_explicit:
                if current is not None:
                    visible_text = current.subtitle or current.body
                    if visible_text:
                        updates["subtitle"] = visible_text
            else:
                text = str(proposed.subtitle or "").strip()
                if self._looks_like_replace_placeholder_text(text):
                    if current is None:
                        return None
                    visible_text = current.subtitle or current.body
                    updates["subtitle"] = visible_text
        else:
            for field_name in ("subtitle", "body"):
                if field_name not in raw:
                    if current is None:
                        continue
                    updates[field_name] = getattr(current, field_name)
                    continue
                text = str(getattr(proposed, field_name) or "").strip()
                if not self._looks_like_replace_placeholder_text(text):
                    continue
                if current is None:
                    return None
                updates[field_name] = getattr(current, field_name)

        for field_name in ("image_description", "image_url"):
            if field_name in raw:
                continue
            if current is None:
                continue
            if get_presentation_template(proposed.template).image_box is None:
                continue
            updates[field_name] = getattr(current, field_name)
        if not updates:
            return proposed
        return Slide.model_validate({**proposed.model_dump(), **updates})

    def _paginate_slide_for_editing(self, slide: Slide) -> list[Slide]:
        """Split an oversized slide into multiple edit-time pages using the export rules."""
        template_spec = get_presentation_template(slide.template)
        if slide.template == "title_subtitle":
            source_text = slide.subtitle or ""
        else:
            source_text = slide.body or ""

        pages = paginate_slide_text(
            source_text,
            chars_per_line=template_spec.pagination.chars_per_line,
            max_lines=template_spec.pagination.max_lines,
        )
        if len(pages) <= 1:
            return [slide]

        base_title = strip_slide_pagination_suffix(slide.title) or slide.title or "未命名页"
        expanded: list[Slide] = []
        total = len(pages)
        for index, page in enumerate(pages, start=1):
            update: dict[str, Any] = {"title": f"{base_title}（{index}/{total}）"}
            if slide.template == "title_subtitle":
                update["subtitle"] = page
                update["body"] = ""
                update["bullet_points"] = []
            else:
                update["body"] = page
                update["bullet_points"] = [line for line in page.splitlines() if line.strip()]
            expanded.append(slide.model_copy(update=update))
        return expanded

    def _rewrite_task_for_overflow(self, task: Task, plan: Plan) -> Task:
        """Replace overflowing single-slide edits with a safe paginated deck rewrite."""
        if task.type != "modify":
            return task

        tool_name = self._resolve_tool_name(task)
        arguments = self._build_task_arguments(task)
        document = self._get_presentation_document(plan)

        if tool_name == "update_slide_content":
            slide_index = arguments.get("slide_index")
            if not isinstance(slide_index, int) or not (0 <= slide_index < len(document.slides)):
                return task
            merged = self._merge_update_into_slide(document.slides[slide_index], arguments)
            pages = self._paginate_slide_for_editing(merged)
            if len(pages) <= 1:
                return task
            slides = list(document.slides)
            slides[slide_index : slide_index + 1] = pages
            return self._build_paginated_replace_task(task, document, slides)

        if tool_name == "add_slide":
            new_slide = self._build_slide_from_add_task(arguments)
            pages = self._paginate_slide_for_editing(new_slide)
            if len(pages) <= 1:
                return task
            insert_before = arguments.get("before_slide_index")
            insert_after = arguments.get("after_slide_index")
            insert_at = len(document.slides)
            if isinstance(insert_before, int) and insert_before >= 0:
                insert_at = min(insert_before, len(document.slides))
            elif isinstance(insert_after, int) and insert_after >= 0:
                insert_at = min(insert_after + 1, len(document.slides))
            slides = list(document.slides)
            slides[insert_at:insert_at] = pages
            return self._build_paginated_replace_task(task, document, slides)

        if tool_name == "replace_presentation":
            payload = {
                "title": arguments.get("title") or document.title,
                "classroom_script": arguments.get("classroom_script") or document.classroom_script,
                "slides": arguments.get("slides") or [slide.model_dump() for slide in document.slides],
            }
            target_document = PresentationDocument.model_validate(payload)
            expanded_slides: list[Slide] = []
            changed = False
            for slide in target_document.slides:
                pages = self._paginate_slide_for_editing(slide)
                if len(pages) > 1:
                    changed = True
                expanded_slides.extend(pages)
            if not changed:
                return task
            return self._build_paginated_replace_task(task, target_document, expanded_slides)

        return task

    def _rewrite_task_for_condense(self, task: Task, plan: Plan) -> Task:
        """Rewrite overflowing slide edits into single-page condensed writes."""
        if task.type != "modify":
            return task

        tool_name = self._resolve_tool_name(task)
        arguments = self._build_task_arguments(task)
        document = self._get_presentation_document(plan)

        if tool_name == "update_slide_content":
            slide_index = arguments.get("slide_index")
            if not isinstance(slide_index, int) or not (0 <= slide_index < len(document.slides)):
                return task
            merged = self._merge_update_into_slide(document.slides[slide_index], arguments)
            condensed = self._condense_slide_for_editing(merged)
            updated_task = task.model_copy(deep=True)
            updated_task.parameters = deepcopy(updated_task.parameters)
            updated_task.parameters["body"] = condensed.body
            if condensed.subtitle is not None or "subtitle" in updated_task.parameters:
                updated_task.parameters["subtitle"] = condensed.subtitle
            return updated_task

        if tool_name == "add_slide":
            condensed = self._condense_slide_for_editing(self._build_slide_from_add_task(arguments))
            updated_task = task.model_copy(deep=True)
            updated_task.parameters = deepcopy(updated_task.parameters)
            updated_task.parameters["body"] = condensed.body
            if condensed.subtitle is not None or "subtitle" in updated_task.parameters:
                updated_task.parameters["subtitle"] = condensed.subtitle
            return updated_task

        if tool_name == "replace_presentation":
            slides = arguments.get("slides")
            if not isinstance(slides, list):
                return task
            updated_task = task.model_copy(deep=True)
            updated_task.parameters = deepcopy(updated_task.parameters)
            updated_task.parameters["slides"] = [
                self._condense_slide_for_editing(Slide.model_validate(raw_slide)).model_dump()
                for raw_slide in slides
            ]
            return updated_task

        return task

    def _build_paginated_replace_task(
        self,
        original_task: Task,
        document: PresentationDocument,
        slides: list[Slide],
    ) -> Task:
        """Encode an auto-pagination rewrite as a replace_presentation task."""
        parameters = {
            "title": document.title,
            "classroom_script": document.classroom_script,
            "slides": [slide.model_dump() for slide in slides],
            "_auto_strategy": "paginate_overflow",
        }
        return Task(
            type=original_task.type,
            tool_name="replace_presentation",
            target=original_task.target,
            action=original_task.action or "rewrite",
            proposed_content=original_task.proposed_content,
            response=original_task.response,
            parameters=parameters,
        )

    def _find_slide_overflow_issue(self, task: Task) -> dict[str, Any] | None:
        """Estimate whether a planned slide write will overflow its target template."""
        if task.type != "modify":
            return None

        tool_name = self._resolve_tool_name(task)
        arguments = self._build_task_arguments(task)
        candidates: list[Slide] = []

        if tool_name == "update_slide_content":
            get_plan = getattr(self.plan_service, "get", None)
            if not callable(get_plan):
                return None
            plan = get_plan(self.plan_id)
            if plan is None or plan.doc_type != "presentation":
                return None
            document = self._get_presentation_document(plan)
            slide_index = arguments.get("slide_index")
            if not isinstance(slide_index, int) or not (0 <= slide_index < len(document.slides)):
                return None
            candidates.append(self._merge_update_into_slide(document.slides[slide_index], arguments))
        elif tool_name == "add_slide":
            candidates.append(self._build_slide_from_add_task(arguments))
        elif tool_name == "replace_presentation":
            slides = arguments.get("slides")
            if not isinstance(slides, list):
                return None
            for raw_slide in slides:
                try:
                    candidates.append(Slide.model_validate(raw_slide))
                except Exception:  # noqa: BLE001
                    continue
        else:
            return None

        for slide in candidates:
            template_spec = get_presentation_template(slide.template)
            source_text = slide.subtitle if slide.template == "title_subtitle" else slide.body
            pages = paginate_slide_text(
                source_text or "",
                chars_per_line=template_spec.pagination.chars_per_line,
                max_lines=template_spec.pagination.max_lines,
            )
            if len(pages) <= 1:
                continue
            return {
                "title": slide.title or "当前页",
                "template": slide.template,
                "page_count": len(pages),
            }
        return None

    def _build_presentation_guard_planner_rules(self) -> str:
        """Allow planner prompts to reuse shared PPT guard-rail wording."""
        return (
            "18a. 当用户只是在保留原内容前提下做局部结构调整，例如新增一页、删一页、复制一页、把某页移到开头/末尾/另一页前后，"
            "优先使用 add_slide / move_slide / delete_slide / duplicate_slide 等局部工具，不要默认改成 replace_presentation。\n"
            "18b. 如果用户要新增封面、标题页、扉页，且没有另行指定位置，默认把它插到第一页前；优先使用 `add_slide.before_slide_index=0`。\n"
            "18c. 只有在同时要重写多页内容、按新叙事整体重做、或自动分页后需要一次性提交完整 deck 时，才优先使用 replace_presentation。\n"
            "18d. 涉及课堂小游戏入口时，智能体只负责判断哪一页承接第几个小游戏；优先在目标 slide 上写 `game_index`（从 1 开始，对应小游戏顺序）。\n"
            "18e. 如必须保留结构化占位，只能使用 `[[GAME_LINK:1]]` 这类占位；不要自由生成 `link_text`、`link_url`、真实游戏 URL，"
            "也不要把小游戏题目、玩法或素材展开成普通正文页。\n"
            "18f. 小游戏真实链接和标准入口文案由程序确定性注入；如果当前不能可靠判断挂载位置，就继续定位或 follow_up，不要硬猜；"
            "未显式绑定的小游戏会由系统兜底追加。\n"
        )

    def _get_pending_follow_up_resume_target(self) -> str:
        """Resume planner follow-ups against the current slide deck."""
        return "当前 PPT"

    def _render_intent_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        follow_up_context: str,
        user_message: str,
    ) -> str:
        """Render a presentation-specific intent prompt from shared planner context."""
        return self.planner.build_presentation_intent_prompt(
            prompt_context=prompt_context,
            follow_up_context=follow_up_context,
            user_message=user_message,
            template_names=", ".join(list_presentation_templates()),
            extra_rules=self._build_presentation_guard_planner_rules(),
        )

    def _get_intent_system_message(self) -> str:
        """Use a presentation-specific system role for the planner intent call."""
        return "你是演示文稿编辑器的意图识别模块。只输出 JSON，不要输出 Markdown 代码块，不要解释。"

    def _get_replan_system_message(self) -> str:
        """Use a presentation-specific system role for the planner replan call."""
        return "你是演示文稿编辑器的继续规划模块。只输出 JSON，不要输出 Markdown 代码块，不要解释。"

    def _render_replan_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        completed_steps: list[str],
        user_message: str,
    ) -> str:
        """Render a presentation-specific replan prompt from shared planner context."""
        return self.planner.build_presentation_replan_prompt(
            prompt_context=prompt_context,
            completed_steps=completed_steps,
            user_message=user_message,
            extra_rules=self._build_presentation_guard_planner_rules(),
        )

    async def _compose_context_snapshot(
        self,
        *,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> str:
        """Build a slide-centric context snapshot for presentation editing."""
        focus_indices = self._select_focus_slide_indices(plan, conversation, recent_ops, user_message)
        blocks = [
            ("演示文稿概要", self._build_presentation_outline(plan)),
            ("用户指向内容", self._build_targeted_slide_context_text(plan, user_message)),
            ("焦点幻灯片", self._build_focus_slides_text(plan, focus_indices)),
            ("会话记忆", self._build_session_memory_text(conversation)),
            ("最近操作", self._summarize_operations(recent_ops)),
        ]
        rendered = []
        for title, body in blocks:
            if body and body.strip():
                rendered.append(f"[{title}]\n{body.strip()}")
        return "\n\n".join(rendered)

    async def _build_system_prompt(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> str:
        """Generate a presentation-oriented system prompt."""
        tools_summary = "\n".join(
            f"- {item.name}: {item.description}" for item in self.tools_registry.list_tools()
        )
        global_preferences = self._get_active_preferences_text(self.user_id)
        temp_preferences = self.conv_service.get_temp_preferences(conversation.id)
        temp_preferences_text = render_temp_preferences_text(temp_preferences)
        context_snapshot = await self._compose_context_snapshot(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message=user_message,
        )
        global_preferences_text = global_preferences or "暂无激活的全局偏好。"
        template_names = ", ".join(list_presentation_templates())

        return (
            "你是演示文稿智能体的文档编辑器。\n"
            "你负责帮助用户创建和修改真实课堂上给学生看的 PPT，而不是把教案原文直接搬进幻灯片。\n"
            "演示文稿内容包含标题、classroom_script 和 slides 列表；slides 中每页通常包含 template、title、subtitle、body、image_description、image_url、notes、source_section。\n"
            f"当前已注册模板：{template_names}。\n"
            "其中 title_subtitle 适合封面或结束页，只显示主标题和可选副标题。\n"
            "当用户提出大范围改版、想按新思路重做整套 PPT、需要把长页拆成多页、或要同时重写多页内容与整体结构时，优先调用 replace_presentation，一次性提交整套 slides。\n"
            "当用户只想精修局部页面时，优先调用 update_slide_content；当用户只想新增、删除、复制或移动少数页面时，优先调用 add_slide、move_slide、delete_slide、duplicate_slide、change_layout 等局部工具。\n"
            "涉及课堂小游戏入口时，智能体只负责判断挂在哪一页、对应第几个小游戏；优先写 `game_index=1/2/3...`。如必须保留占位，最多只写 `[[GAME_LINK:1]]` 这类结构化占位。\n"
            "不要自由生成小游戏 `link_text`、`link_url`、真实 URL，也不要把小游戏题目、翻卡内容、抢答内容或玩法展开成普通正文页；真实链接和标准入口文案由程序确定性注入，未绑定的小游戏会由系统兜底追加到末尾。\n"
            "如果用户要新增封面、标题页、扉页，而没有特别说明位置，默认把它插到第一页前，不要追加到末尾。\n"
            "在生成任何写回 PPT 的正文前，先判断当前模板能否装下这段内容；长代码、长段落、长列表不能默认硬塞进单页。\n"
            "如果用户明确要保留完整内容、说这页放不下、要求分页或拆页，优先保留内容并拆成多页；如果用户明确要求精简、概览或只保留要点，才改成单页压缩版。\n"
            "如果内容会溢出，但用户没有说明要分页还是精简，先 ask_follow_up 澄清，不要擅自默认精简化。\n"
            "只要前一步会新增、删除、复制、移动页面，后续就不要继续沿用旧的 slide_index / after_slide_index / before_slide_index / new_index；要基于最新 PPT 重新定位。\n"
            "用户口中的页码通常从 1 开始，而工具参数 slide_index / after_slide_index / before_slide_index / new_index 从 0 开始；规划工具参数时必须换算。\n"
            "如果用户只是没说清页码或原文，不要立刻追问；优先用 get_presentation_outline、search_in_presentation、get_slide_details 先定位。\n"
            "如果 search_in_presentation 没有精确命中，不要把相似结果直接当成已定位完成；要继续核实后再修改。\n"
            "如果用户要把分页内容合并回单页、明确说“不要分页”，或搜索结果命中连续的同名分页页，必须先用 get_slide_details 读出相关页正文，再生成最终修改；不要先把“准备合并”“先搜索后填充”这类过程说明写进 PPT。\n"
            "如果用户需要外部 Demo 或在线链接，优先用 search_web 找候选链接，再写回目标幻灯片。\n"
            "如果 search_web 当前不可用或没有结果，不要中断整轮处理；要明确说明无法联网核验，并继续依据当前 PPT、教案上下文和模型已有知识给出可落地的修改。\n"
            "如果用户要求把某页改成可插图、可放截图、运行结果截图页，优先使用带图片区的模板，并把 image_description 当作截图占位说明；不要因为缺少 image_url 就停下来追问。\n"
            "如果用户要求移除图片占位、去掉这页图片或改成纯文字版式，优先切到不带图片区的模板，并清理这页旧的 image_description / image_url。\n"
            "当用户目标缺少关键条件、无法可靠执行时，优先调用 ask_follow_up 提出一个明确的澄清问题，不要擅自猜测。\n"
            "当操作可能删除、覆盖或显著改写内容时，优先调用 request_confirmation 请求用户确认，再执行目标工具。\n"
            "如果工具返回错误，请解释原因并给出下一步建议。\n"
            "优先保持页面结构清晰，标题简洁，正文适合投影阅读，避免写成教师备课提纲。\n"
            "不要套用固定的 PPT 流程、固定页数或固定页型组合；是否需要封面、目录、过渡页、总结页，完全取决于当前教案和用户需求。\n\n"
            f"当前演示文稿上下文：\n{context_snapshot}\n\n"
            f"全局偏好注入：\n{global_preferences_text}\n\n"
            f"临时偏好：{temp_preferences_text}\n\n"
            f"可用工具：\n{tools_summary}"
        )

    def _build_intent_failure_follow_up(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask a presentation-specific clarification question when intent recognition fails."""
        focus_indices = self._select_focus_slide_indices(plan, conversation, recent_ops, user_message)
        slides = self._get_presentation_slides(plan)
        focus_labels = [
            f"第 {index + 1} 页《{self._get_slide_title(slides[index], index)}》"
            for index in focus_indices[:2]
            if 0 <= index < len(slides)
        ]
        return self.planner.build_ambiguous_intent_follow_up(
            focus_labels=focus_labels,
            focus_kind="页面",
            pending_follow_up=pending_follow_up,
            pending_question_template=(
                "我还没能把你的补充信息稳定映射成具体 PPT 修改。"
                "{focus_sentence}"
                "请直接告诉我要改哪一页，或贴出要替换的原句/目标文案，我继续处理。"
            ),
            initial_question_template=(
                "我先基于当前 PPT 做了判断，但你的意图还不够明确。"
                "{focus_sentence}"
                "你是想修改这一页吗？如果是，请直接补充要怎么改；如果不是，也请告诉我目标页码、标题或原句。"
            ),
        )

    def _get_missing_plan_message(self) -> str:
        """Return the presentation-specific not-found message."""
        return "未找到对应演示文稿。"

    def _validate_task_queue(
        self,
        tasks: list[Task],
        *,
        quality_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Block PPT task batches that would invalidate slide indices mid-flight."""
        reorder_task: Task | None = None
        for task in tasks:
            if task.type not in {"modify", "query"}:
                continue
            tool_name = self._resolve_tool_name(task)
            arguments = self._build_task_arguments(task)
            if self._is_empty_replace_presentation(tool_name, arguments):
                return self._build_empty_replace_presentation_follow_up(arguments)
            placeholder_issue = self._find_replace_presentation_placeholder_issue(tool_name, arguments)
            if placeholder_issue is not None:
                return self._build_replace_presentation_placeholder_follow_up(placeholder_issue)
            if reorder_task is not None and self._task_uses_fixed_slide_position(tool_name, arguments):
                return self._build_slide_reorder_follow_up(reorder_task, task)
            if tool_name in STRUCTURE_CHANGING_SLIDE_TOOLS:
                reorder_task = task
            overflow_issue = self._find_slide_overflow_issue(task)
            if overflow_issue is not None and self._should_block_for_overflow(quality_context):
                return self._build_slide_overflow_follow_up(overflow_issue)
            if self._should_block_local_request_replace(tool_name, arguments, quality_context):
                return self._build_replace_presentation_scope_follow_up(arguments)
        return None

    def _is_empty_replace_presentation(self, tool_name: str | None, arguments: dict[str, Any]) -> bool:
        """Detect unsafe full-deck rewrites that forgot to include the rebuilt slides."""
        if tool_name != "replace_presentation":
            return False
        slides = arguments.get("slides")
        return not isinstance(slides, list) or len(slides) == 0

    def _build_empty_replace_presentation_follow_up(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Stop empty replace-presentation calls before they reach confirmation."""
        slides = arguments.get("slides")
        slides_count = len(slides) if isinstance(slides, list) else 0
        question = (
            "我先暂停了这次整体替换，因为当前计划里的 `replace_presentation` 没有给出重排后的完整幻灯片列表，"
            f"现在只有 {slides_count} 页。"
            "如果直接执行，会把现有 PPT 清空。"
            "如果你是想移动页序、删页或插页，请回复“按局部修改”；"
            "只有在我已经准备好完整重排后的全部页面时，才适合回复“整体重排”。"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": ["按局部修改", "整体重排", "取消这次修改"],
            "follow_up_kind": "empty_replace_guard",
        }

    def _find_replace_presentation_placeholder_issue(
        self,
        tool_name: str | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Detect whole-deck rewrites that still contain placeholder copy."""
        if tool_name != "replace_presentation":
            return None
        if str(arguments.get("_auto_strategy") or "").strip() == "paginate_overflow":
            return None

        slides = arguments.get("slides")
        if not isinstance(slides, list):
            return None

        flagged: list[dict[str, Any]] = []
        for index, raw_slide in enumerate(slides):
            slide = raw_slide if isinstance(raw_slide, dict) else {}
            for field_name in ("subtitle", "body", "notes"):
                text = str(slide.get(field_name) or "").strip()
                if self._looks_like_replace_placeholder_text(text):
                    flagged.append(
                        {
                            "slide_index": index,
                            "title": str(slide.get("title") or f"第 {index + 1} 页").strip(),
                            "field": field_name,
                            "text": text,
                        }
                    )
                    break
        if not flagged:
            return None
        return {"flagged": flagged, "slides_count": len(slides)}

    def _looks_like_replace_placeholder_text(self, text: str) -> bool:
        """Flag short placeholder copy that would dangerously overwrite existing slide text."""
        if not text:
            return False
        normalized = re.sub(r"\s+", "", text)
        if "http://" in normalized.lower() or "https://" in normalized.lower():
            return False
        if len(normalized) <= 24 and any(pattern.search(normalized) for pattern in REPLACE_PLACEHOLDER_TEXT_PATTERNS):
            return True
        return any(pattern.search(text) for pattern in REPLACE_PLACEHOLDER_TEXT_PATTERNS[1:])

    def _looks_like_nonfinal_slide_text(self, text: str) -> bool:
        """Detect process/meta copy that should never be written into visible slide fields."""
        if self._looks_like_replace_placeholder_text(text):
            return True

        normalized = re.sub(r"\s+", "", str(text or "").strip())
        if not normalized:
            return False
        if "http://" in normalized.lower() or "https://" in normalized.lower():
            return False
        return any(pattern.search(normalized) for pattern in NONFINAL_SLIDE_TEXT_PATTERNS)

    def _build_replace_presentation_placeholder_follow_up(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Block deck rewrites that still contain placeholder copy such as ellipses."""
        flagged = issue.get("flagged") if isinstance(issue, dict) else []
        rendered: list[str] = []
        if isinstance(flagged, list):
            for item in flagged[:3]:
                if not isinstance(item, dict):
                    continue
                slide_number = int(item.get("slide_index") or 0) + 1
                title = str(item.get("title") or f"第 {slide_number} 页").strip()
                field_label = REPLACE_PLACEHOLDER_FIELD_LABELS.get(str(item.get("field") or "").strip(), "内容")
                preview = self._truncate_text(str(item.get("text") or "").strip(), 20)
                rendered.append(f"第 {slide_number} 页《{title}》的{field_label}仍是“{preview}”")

        detail_text = "；".join(rendered) if rendered else "有页面内容仍像省略稿"
        question = (
            "我先暂停了这次整体替换，因为重排结果里有页面内容仍像占位稿或省略稿，"
            f"{detail_text}。"
            "如果现在执行，会把原来的完整内容整体覆盖掉。"
            "请改成保留未点名页面的原内容，或提供完整重排后的最终文案。"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": ["保留其余页面原内容", "我会提供完整重排文案", "取消这次修改"],
            "follow_up_kind": "replace_content_guard",
        }

    def _validate_task_content_quality(
        self,
        task: Task,
        quality_context: dict[str, Any] | None,
    ) -> list[str] | None:
        """Block PPT writes that still contain process placeholders instead of final slide copy."""
        issues = super()._validate_task_content_quality(task, quality_context) or []
        if task.type != "modify":
            return issues or None

        tool_name = self._resolve_tool_name(task)
        if tool_name not in {"update_slide_content", "add_slide"}:
            return issues or None

        arguments = self._build_task_arguments(task)
        for field_name, field_label in SLIDE_WRITE_FIELD_LABELS.items():
            value = arguments.get(field_name)
            if not isinstance(value, str) or not value.strip():
                continue
            if not self._looks_like_nonfinal_slide_text(value):
                continue
            preview = self._truncate_text(value.replace("\n", " "), 40)
            issues.append(f"{field_label}仍像过程性占位文案“{preview}”")
        return issues or None

    def _build_content_quality_follow_up(self, task: Task, issues: list[str]) -> dict[str, Any]:
        """Use PPT-specific wording when blocking placeholder slide copy."""
        tool_name = self._resolve_tool_name(task)
        if tool_name in {"update_slide_content", "add_slide"} and any("过程性占位文案" in issue for issue in issues):
            issue_text = "；".join(issue for issue in issues if issue)
            action = "修改这页 PPT" if tool_name == "update_slide_content" else "新增这一页 PPT"
            question = (
                f"我先暂停了这次{action}，因为当前写入内容仍像过程说明或占位稿：{issue_text}。"
                "请直接给最终要显示在幻灯片上的标题/正文，或让我先继续定位原文后再改。"
            )
            return {
                "type": "follow_up",
                "question": question,
                "options": None,
            }
        return super()._build_content_quality_follow_up(task, issues)

    def _should_block_local_request_replace(
        self,
        tool_name: str | None,
        arguments: dict[str, Any],
        quality_context: dict[str, Any] | None,
    ) -> bool:
        """Block whole-deck replacement when the user's wording sounds local in scope."""
        if tool_name != "replace_presentation":
            return False
        if str(arguments.get("_auto_strategy") or "").strip() == "paginate_overflow":
            return False

        request_text = str((quality_context or {}).get("request_text") or "").strip().lower()
        if not request_text or not self._looks_like_local_scope_request(request_text):
            return False

        return True

    def _should_block_for_overflow(self, quality_context: dict[str, Any] | None) -> bool:
        """Ask before overflowing slide edits when the user did not choose paginate vs condense."""
        if not quality_context:
            return True
        if quality_context.get("prefers_paginate"):
            return False
        if quality_context.get("prefers_condense"):
            return False
        return True

    def _build_slide_overflow_follow_up(self, overflow_issue: dict[str, Any]) -> dict[str, Any]:
        """Ask how to resolve a slide that cannot fit on one page."""
        title = str(overflow_issue.get("title") or "当前页").strip()
        page_count = int(overflow_issue.get("page_count") or 2)
        template = str(overflow_issue.get("template") or "title_body").strip()
        question = (
            f"我先暂停了这次修改，因为“{title}”按当前模板 `{template}` 预计至少要 {page_count} 页才能完整显示。"
            "你是希望我自动分页并保留完整内容，还是改成单页精简版？"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": ["自动分页保留完整内容", "改成单页精简版", "取消这次修改"],
            "follow_up_kind": "slide_overflow_guard",
        }

    def _build_unresolved_local_reference_follow_up(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> TaskList | None:
        """Pause before planning when a deictic slide reference still cannot be grounded."""
        if pending_follow_up is not None:
            return None

        message = str(user_message or "").strip().lower()
        if not message:
            return None
        if any(token in message for token in GLOBAL_REWRITE_REQUEST_HINTS):
            return None
        if not any(token in message for token in AMBIGUOUS_LOCAL_REFERENCE_HINTS):
            return None

        slides = self._get_presentation_slides(plan)
        if len(slides) <= 1:
            return None
        if self._find_explicit_slide_indices(slides, user_message):
            return None
        if self._find_targeted_slide_hits(plan, user_message):
            return None

        recent_slide_indices = self._collect_recent_context_slide_indices(recent_ops)
        if len(recent_slide_indices) == 1:
            return None

        payload = self._build_intent_failure_follow_up(
            plan,
            conversation,
            recent_ops,
            user_message,
            pending_follow_up=None,
        )
        return TaskList(
            goal_status="need_follow_up",
            tasks=[
                Task(
                    type="follow_up",
                    parameters={
                        "question": payload["question"],
                        "options": payload.get("options"),
                    },
                )
            ],
        )

    def _collect_recent_context_slide_indices(self, recent_ops: list[Operation]) -> set[int]:
        """Extract recently focused slide indices that can disambiguate “这页/那页” references."""
        indices: set[int] = set()
        for operation in recent_ops[-4:]:
            arguments = operation.arguments if isinstance(operation.arguments, dict) else {}
            result = operation.result if isinstance(operation.result, dict) else {}

            for key in POSITIONAL_SLIDE_KEYS:
                value = arguments.get(key)
                if isinstance(value, int) and value >= 0:
                    indices.add(value)

            slide = result.get("slide")
            if isinstance(slide, dict):
                slide_index = slide.get("slide_index")
                if isinstance(slide_index, int) and slide_index >= 0:
                    indices.add(slide_index)

            matches = result.get("matches")
            if isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict):
                slide_index = matches[0].get("slide_index")
                if isinstance(slide_index, int) and slide_index >= 0:
                    indices.add(slide_index)
        return indices

    def _looks_like_local_scope_request(self, request_text: str) -> bool:
        """Infer whether the user asked for targeted slide edits instead of a deck-wide rewrite."""
        if any(token in request_text for token in GLOBAL_REWRITE_REQUEST_HINTS):
            return False
        if re.search(r"第\s*\d+\s*页", request_text, flags=re.IGNORECASE):
            return True
        if re.search(r"(删|删除|去掉|加|添加|新增|插入|补).{0,6}页", request_text, flags=re.IGNORECASE):
            return True
        return any(token in request_text for token in LOCAL_SCOPE_REQUEST_HINTS)

    def _looks_like_structural_local_request(self, request_text: str) -> bool:
        """Allow replace_presentation when a local request still requires page-order changes."""
        if any(token in request_text for token in PAGINATION_REQUEST_HINTS):
            return True
        if any(token in request_text for token in STRUCTURAL_LOCAL_REQUEST_HINTS):
            return True
        return bool(
            re.search(
                r"((删|删除|去掉|新增|添加|插入|补|复制|移动|移到|挪到|调到|放到|调换|交换).{0,8}(页|页面|幻灯片))"
                r"|((页|页面|幻灯片).{0,8}(删|删除|去掉|新增|添加|插入|补|复制|移动|移到|挪到|调到|放到|调换|交换))",
                request_text,
                flags=re.IGNORECASE,
            )
        )

    def _get_current_slide_count(self) -> int | None:
        """Read the current deck size for replace-presentation safety heuristics."""
        get_plan = getattr(self.plan_service, "get", None)
        if not callable(get_plan):
            return None
        plan = get_plan(self.plan_id)
        if plan is None or plan.doc_type != "presentation":
            return None
        return len(self._get_presentation_slides(plan))

    def _build_replace_presentation_scope_follow_up(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Ask the user to choose between a local edit path and an intentional full rewrite."""
        slides = arguments.get("slides")
        slides_count = len(slides) if isinstance(slides, list) else 0
        question = (
            "我先暂停了这次 PPT 修改，因为你的需求看起来更像局部改页，"
            f"但当前计划却要用 `replace_presentation` 整体覆盖整份演示文稿（预计写入 {slides_count} 页）。"
            "这种做法很容易把没点名的页面也一起改掉。"
            "如果你只是想删一页、加一页或改少数几页，请回复“按局部修改”；"
            "只有你明确要整套重排时，再回复“整体重排”。"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": ["按局部修改", "整体重排", "取消这次修改"],
            "follow_up_kind": "replace_scope_guard",
        }

    def _build_generic_operation_description(self, task: Task, tool_name: str, arguments: dict[str, Any]) -> str:
        """Make destructive PPT confirmations read more clearly."""
        if tool_name == "replace_presentation":
            slides = arguments.get("slides")
            slides_count = len(slides) if isinstance(slides, list) else 0
            if str(arguments.get("_auto_strategy") or "").strip() == "paginate_overflow":
                return f"准备按页面容量自动分页并重写相关幻灯片（结果共 {slides_count} 页）"
            return f"准备整体替换整份 PPT（共 {slides_count} 页）"
        return super()._build_generic_operation_description(task, tool_name, arguments)

    def _render_arguments_preview(self, arguments: dict[str, Any]) -> str:
        """Render replace-presentation previews in a slide-aware format."""
        slides = arguments.get("slides")
        if isinstance(slides, list):
            strategy = str(arguments.get("_auto_strategy") or "").strip()
            prefix = "将按页面容量自动分页，写入" if strategy == "paginate_overflow" else "将整体写入"
            lines = [f"{prefix} {len(slides)} 页幻灯片："]
            for index, raw_slide in enumerate(slides[:5], start=1):
                slide = raw_slide if isinstance(raw_slide, dict) else {}
                title = str(slide.get("title") or f"第 {index} 页").strip()
                template = str(slide.get("template") or slide.get("layout") or "未设置").strip()
                preview = (
                    str(slide.get("subtitle") or "").strip()
                    or str(slide.get("body") or "").strip()
                    or str(slide.get("image_description") or "").strip()
                    or "无正文摘要"
                )
                lines.append(f"- 第 {index} 页 | {title} | 模板：{template} | {self._truncate_text(preview, 80)}")
            if len(slides) > 5:
                lines.append(f"- 其余 {len(slides) - 5} 页也会一起覆盖。")
            return "\n".join(lines)
        return super()._render_arguments_preview(arguments)

    def _task_uses_fixed_slide_position(self, tool_name: str | None, arguments: dict[str, Any]) -> bool:
        """Return whether a task depends on a concrete slide index in the current deck."""
        if tool_name == "replace_presentation":
            return False
        if tool_name == "add_slide":
            before_slide_index = arguments.get("before_slide_index")
            after_slide_index = arguments.get("after_slide_index")
            return (
                isinstance(before_slide_index, int)
                and before_slide_index >= 0
                or isinstance(after_slide_index, int)
                and after_slide_index >= 0
            )
        return any(isinstance(arguments.get(key), int) and int(arguments[key]) >= 0 for key in POSITIONAL_SLIDE_KEYS)

    def _build_slide_reorder_follow_up(self, first_task: Task, conflicting_task: Task) -> dict[str, Any]:
        """Explain why the queued PPT edits are unsafe and ask for a safer route."""
        first_tool = self._resolve_tool_name(first_task) or "modify"
        first_arguments = self._build_task_arguments(first_task)
        conflicting_tool = self._resolve_tool_name(conflicting_task) or "modify"
        conflicting_arguments = self._build_task_arguments(conflicting_task)
        first_subject = self._infer_task_subject(first_arguments, self._clean_text(first_task.target))
        conflicting_subject = self._infer_task_subject(conflicting_arguments, self._clean_text(conflicting_task.target))
        question = (
            "我先暂停了这批 PPT 修改，因为其中前一步会改变页序，后一步还在按旧页码继续操作，容易改错页。"
            f"当前检测到的组合是：先用 {first_tool} 处理“{first_subject}”，"
            f"再用 {conflicting_tool} 处理“{conflicting_subject}”。"
            "更稳妥的做法是改成一次性 `replace_presentation` 重排相关页，或只先执行第一步后再基于最新页码继续。"
            "你回复“整体重排”我就按 replace_presentation 继续；回复“只做第一步”我就只保留首个结构改动。"
        )
        return {
            "type": "follow_up",
            "question": question,
            "options": ["整体重排", "只做第一步", "取消这批修改"],
            "follow_up_kind": "slide_reorder_conflict",
        }

    def _get_presentation_slides(self, plan: Plan) -> list[dict[str, Any]]:
        """Read the deck slides from plan content."""
        content = plan.content if isinstance(plan.content, dict) else {}
        slides = content.get("slides")
        if not isinstance(slides, list):
            return []
        return [slide for slide in slides if isinstance(slide, dict)]

    def _get_slide_title(self, slide: dict[str, Any], index: int) -> str:
        """Build a readable slide title."""
        title = str(slide.get("title") or "").strip()
        return title or f"第 {index + 1} 页"

    def _align_explicit_slide_indices(self, task: Task, arguments: dict[str, Any]) -> dict[str, Any]:
        """Repair the common off-by-one drift when the planner copied a human page number directly."""
        explicit_indices = self._extract_task_explicit_slide_indices(task)
        if len(explicit_indices) != 1:
            return arguments

        corrected = deepcopy(arguments)
        explicit_index = explicit_indices[0]
        for key in POSITIONAL_SLIDE_KEYS:
            value = corrected.get(key)
            if isinstance(value, int) and value == explicit_index + 1:
                corrected[key] = explicit_index
        return corrected

    def _extract_task_explicit_slide_indices(self, task: Task) -> list[int]:
        """Look for a single explicit “第 N 页” reference in task-local text fields."""
        indices: list[int] = []
        seen: set[int] = set()
        texts = [
            task.target,
            task.action,
            task.response,
        ]
        for key in ("title_keyword", "target_text", "keyword", "query"):
            value = task.parameters.get(key)
            if isinstance(value, str):
                texts.append(value)

        for text in texts:
            if not isinstance(text, str) or not text.strip():
                continue
            for index in self._extract_explicit_slide_number_indices(text):
                if index not in seen:
                    seen.add(index)
                    indices.append(index)
        return indices

    def _summarize_slide_preview(self, slide: dict[str, Any]) -> str:
        """Build a one-line summary for a slide."""
        for field_name in ("subtitle", "body", "image_description", "notes"):
            value = str(slide.get(field_name) or "").strip()
            if value:
                return self._truncate_text(value.replace("\n", " "), 70)
        bullet_points = slide.get("bullet_points")
        if isinstance(bullet_points, list) and bullet_points:
            return self._truncate_text("；".join(str(item).strip() for item in bullet_points if str(item).strip()), 70)
        return "无正文摘要"

    def _build_presentation_outline(self, plan: Plan) -> str:
        """Summarize presentation metadata and slide outline."""
        content = plan.content if isinstance(plan.content, dict) else {}
        slides = self._get_presentation_slides(plan)
        classroom_script = str(content.get("classroom_script") or "").strip()

        lines = [
            f"标题：{content.get('title') or plan.title}",
            f"类型：{plan.doc_type}",
            f"学科：{plan.subject or '未提供'}",
            f"年级：{plan.grade or '未提供'}",
            f"幻灯片数：{len(slides)}",
        ]
        if classroom_script:
            lines.append(f"课堂内容稿摘要：{self._truncate_text(classroom_script.replace(chr(10), ' '), 120)}")
        for index, slide in enumerate(slides[:12]):
            template = str(slide.get("template") or slide.get("layout") or "未设置").strip()
            lines.append(
                f"- 第 {index + 1} 页 | {self._get_slide_title(slide, index)} | 模板：{template} | {self._summarize_slide_preview(slide)}"
            )
        if len(slides) > 12:
            lines.append(f"- 其余 {len(slides) - 12} 页已省略")
        return "\n".join(lines)

    def _build_focus_slides_text(self, plan: Plan, focus_indices: list[int]) -> str:
        """Render only the most relevant slides for the current round."""
        slides = self._get_presentation_slides(plan)
        if not slides:
            return "当前 PPT 没有可用幻灯片。"

        rendered: list[str] = []
        for index in focus_indices[:MAX_CONTEXT_SECTIONS]:
            if not (0 <= index < len(slides)):
                continue
            slide = slides[index]
            payload = {
                "title": self._get_slide_title(slide, index),
                "template": slide.get("template") or slide.get("layout"),
                "subtitle": slide.get("subtitle"),
                "body": slide.get("body"),
                "bullet_points": slide.get("bullet_points"),
                "image_description": slide.get("image_description"),
                "image_url": slide.get("image_url"),
                "notes": slide.get("notes"),
                "source_section": slide.get("source_section"),
            }
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)
            rendered.append(
                f"第 {index + 1} 页 | {self._get_slide_title(slide, index)}\n"
                f"{self._truncate_text(serialized, MAX_SECTION_CONTEXT_CHARS)}"
            )
        return "\n\n".join(rendered)

    def _extract_explicit_slide_number_indices(self, user_message: str) -> list[int]:
        """Find slide numbers explicitly mentioned by the user."""
        indices: list[int] = []
        for raw in re.findall(r"第\s*(\d+)\s*页", user_message or "", flags=re.IGNORECASE):
            try:
                index = int(raw) - 1
            except ValueError:
                continue
            if index >= 0 and index not in indices:
                indices.append(index)
        return indices

    def _find_explicit_slide_indices(self, slides: list[dict[str, Any]], user_message: str) -> list[int]:
        """Find slides explicitly named by index or title."""
        message = (user_message or "").strip().lower()
        if not message:
            return []

        indices = self._extract_explicit_slide_number_indices(message)
        for index, slide in enumerate(slides):
            title = self._get_slide_title(slide, index).strip().lower()
            if title and title in message and index not in indices:
                indices.append(index)
        return indices

    def _find_targeted_slide_hits(self, plan: Plan, user_message: str) -> list[dict[str, Any]]:
        """Find exact quoted text the user appears to be pointing at inside slides."""
        slides = self._get_presentation_slides(plan)
        quoted_fragments = self._extract_quoted_fragments(user_message)
        if not slides or not quoted_fragments:
            return []

        hits: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        for index, slide in enumerate(slides):
            title = self._get_slide_title(slide, index)
            for path, text in self._collect_string_leaves(slide, path="slide"):
                lowered_text = text.lower()
                for fragment in quoted_fragments:
                    if fragment.lower() not in lowered_text:
                        continue
                    key = (index, path, fragment)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        {
                            "slide_index": index,
                            "title": title,
                            "path": path,
                            "needle": fragment,
                            "snippet": self._make_search_snippet(text, fragment),
                        }
                    )
                    if len(hits) >= 6:
                        return hits
        return hits

    def _build_targeted_slide_context_text(self, plan: Plan, user_message: str) -> str:
        """Surface slide/page hits from the user's wording."""
        slides = self._get_presentation_slides(plan)
        if not slides:
            return ""

        lines: list[str] = []
        explicit_indices = self._find_explicit_slide_indices(slides, user_message)
        if explicit_indices:
            labels = [
                f"第 {index + 1} 页《{self._get_slide_title(slides[index], index)}》"
                for index in explicit_indices[:3]
                if 0 <= index < len(slides)
            ]
            if labels:
                lines.append(f"用户明确点名页面：{'、'.join(labels)}")

        text_hits = self._find_targeted_slide_hits(plan, user_message)
        if text_hits:
            lines.append("用户消息中引用/指向的原文命中：")
            for item in text_hits[:4]:
                lines.append(
                    f"- 第 {int(item['slide_index']) + 1} 页《{item['title']}》 | {item['path']} | 命中“{item['needle']}”：{item['snippet']}"
                )
        else:
            quoted_fragments = self._extract_quoted_fragments(user_message)
            if quoted_fragments:
                lines.append("用户引用了具体原句，但当前 PPT 上下文里没有精确命中：")
                for fragment in quoted_fragments[:3]:
                    lines.append(f"- {fragment}")

        return "\n".join(lines)

    def _select_focus_slide_indices(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> list[int]:
        """Pick the most relevant slides for this round."""
        slides = self._get_presentation_slides(plan)
        if not slides:
            return []

        explicit_indices = set(self._find_explicit_slide_indices(slides, user_message))
        targeted_hit_indices = {
            item["slide_index"]
            for item in self._find_targeted_slide_hits(plan, user_message)
            if isinstance(item.get("slide_index"), int)
        }
        recent_slide_indices = {
            int(operation.arguments["slide_index"])
            for operation in recent_ops[-3:]
            if isinstance(operation.arguments, dict) and isinstance(operation.arguments.get("slide_index"), int)
        }

        combined_text = " ".join(
            part
            for part in [
                user_message,
                " ".join(str(item.get("content") or "") for item in self._get_recent_turns(conversation)[-3:]),
                " ".join(
                    str(
                        operation.arguments.get("title_keyword")
                        or operation.arguments.get("keyword")
                        or operation.arguments.get("query")
                        or ""
                    )
                    for operation in recent_ops[-3:]
                    if isinstance(operation.arguments, dict)
                ),
            ]
            if part
        ).lower()
        terms = self._extract_candidate_terms(combined_text)

        scored: list[tuple[int, int]] = []
        for index, slide in enumerate(slides):
            title = self._get_slide_title(slide, index).lower()
            serialized = json.dumps(slide, ensure_ascii=False).lower()
            score = 0
            if index in explicit_indices:
                score += 20
            if index in targeted_hit_indices:
                score += 24
            if index in recent_slide_indices:
                score += 10
            if title and title in combined_text:
                score += 8
            for term in terms:
                if term in title or (title and title in term):
                    score += 4
                elif term in serialized:
                    score += 1
            scored.append((score, index))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = [index for score, index in scored if score > 0][:2]
        if not selected:
            selected = [0]
            if len(slides) > 1:
                selected.append(1)

        expanded: list[int] = []
        for index in selected:
            for candidate in (index - 1, index, index + 1):
                if 0 <= candidate < len(slides) and candidate not in expanded:
                    expanded.append(candidate)
                if len(expanded) >= MAX_CONTEXT_SECTIONS:
                    break
            if len(expanded) >= MAX_CONTEXT_SECTIONS:
                break
        return expanded
