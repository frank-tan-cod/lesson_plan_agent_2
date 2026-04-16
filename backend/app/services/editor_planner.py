"""Shared planner helpers for prompts, task-plan parsing, and follow-up payloads."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from ..schemas import Task, TaskList


@dataclass(slots=True)
class PlannerPromptContext:
    """Shared prompt inputs assembled by the editor before planner rendering."""

    tools_summary: str
    ops_summary: str
    recent_tool_results: str
    completion_criteria: str
    context_snapshot: str


class EditorPlanner:
    """Encapsulate planner prompts, LLM calls, and follow-up payload shaping."""

    def __init__(
        self,
        *,
        json_ready: Callable[[Any], Any],
        truncate_text: Callable[[str, int], str],
    ) -> None:
        self.json_ready = json_ready
        self.truncate_text = truncate_text

    def build_completion_criteria_text(self) -> str:
        """Describe what counts as task completion for planner decisions."""
        return (
            "1. 查询类需求：只有当用户要的信息已经查到并可直接回答时，才算完成。\n"
            "2. 修改类需求：只有当必要修改已经被规划并准备执行，或已实际执行完成时，才算完成；仅完成定位、搜索、读取原文不算完成。\n"
            "3. 多步骤需求：只有当用户要求的每个关键子任务都已覆盖，才算完成；不能只完成前半段。\n"
            "4. 若还缺页码、原句、目标文案、外部链接或确认信息，且缺口会影响可靠落地，就不算完成。\n"
            "5. 如果当前只是得到中间事实、候选位置、候选链接或局部上下文，这通常意味着还需要继续规划，而不是结束。"
        )

    def build_intent_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        follow_up_context: str,
        user_message: str,
        extra_rules: str = "",
    ) -> str:
        """Construct the shared intent-recognition prompt."""
        return (
            "分析用户对当前文档的意图，输出任务列表 JSON。\n"
            "输出格式必须是一个对象：{\"goal_status\": \"complete|need_more_steps|need_follow_up\", \"tasks\": [...]}。\n"
            "goal_status 含义：\n"
            "- complete: 本轮用户目标已完成，或只需给出最终回复。\n"
            "- need_more_steps: 还需要继续执行任务，tasks 中应给出下一步任务。\n"
            "- need_follow_up: 还缺关键信息，tasks 中应给出 follow_up 任务。\n"
            "每个任务字段：\n"
            "- type: modify | query | follow_up | reply | confirm | cancel\n"
            "- tool_name: 需要执行的工具名，必须填写，并且必须来自下方可用工具。\n"
            "- target: 目标位置，例如章节名、幻灯片标题、页码描述。\n"
            "- action: 动作，例如 simplify、rewrite、delete、insert、search、adjust_duration。\n"
            "- proposed_content: 给用户预览的最终改动内容摘要。若涉及写入正文，这里应是最终会落入教案的文本摘要，而不是“添加一段代码”这种操作指令。\n"
            "- response: 当 type=reply 时，直接给用户的自然语言回答。\n"
            "- parameters: 必须是所选工具的最终参数对象，字段名、必填项和枚举值都要严格匹配工具 schema；不要依赖后端猜测或补全。\n"
            "规则：\n"
            "1. 复合指令要拆成多个任务，保持原始顺序。\n"
            "2. 用户是查询时，优先返回 query 任务并令 goal_status=need_more_steps；如果不需要工具即可回答，令 goal_status=complete 并返回 reply 任务。\n"
            "3. 用户是修改时，不要先返回 search_in_plan 再结束；必须直接规划出最终修改工具和最终参数，并令 goal_status=need_more_steps。\n"
            "4. 若关键信息不足，令 goal_status=need_follow_up，并返回一个 follow_up 任务；把问题写入 parameters.question，可选按钮写入 parameters.options。\n"
            "5. 如果用户明确要确认或取消，只返回 confirm 或 cancel 任务，并令 goal_status=need_more_steps。\n"
            "6. 只使用下方可用工具中的工具名。\n"
            "7. 不要输出预设问答模板，不要复用固定答案；所有问题和回答都必须紧扣当前教案、最近操作和本轮用户消息。\n"
            "8. 所有枚举值必须使用工具 schema 中的原始英文值，例如 start、end、after_paragraph、before、after；不要输出“结尾”“开头”等中文值。\n"
            "9. 不要把用户的操作要求原样塞进 content 或 new_content。只有当该文本就是最终要写入教案的内容时，才能放进去。\n"
            "10. 当用户要求新增完整部分、完整示例、完整流程、完整代码、完整讲解时，应先生成真正要写入教案的正文，再选择能承载完整正文的工具；`insert_element` 仅用于提问、案例、活动、板书等简短教学元素。\n"
            "11. 如果你还不能可靠地产生最终写入文本，就返回 follow_up，不要用空内容或指令性文字调用修改工具。\n"
            "12. 如果无需任何工具、且只要直接答复用户，就令 goal_status=complete 并返回 reply 任务；不要用空 tasks 代替状态判断。\n"
            "13. 如果当前消息是在回答 pending_follow_up，默认目标是继续完成上一轮任务；除非用户明确撤销或改目标，不要把补充回答重新规划成新的待确认修改。\n"
            "14. 不要为了“更完整”擅自追加用户未明确要求的衍生修改，例如 adjust_duration、重排章节、补充总结；只有用户明确提出，或这是完成原目标不可缺少的一步时才能添加。\n"
            "15. 当用户要求完整正文、完整示例、完整代码、可运行实现时，写入 content/new_content 的必须是可直接落稿的最终内容，不要出现“简化示例”“按实际情况调整”“自行补充”等占位表述。\n"
            "16. 是否已完成本轮用户需求，必须按下方“任务完成判据”判断；不要把“已经查到位置/已经读到内容”误判成“已经完成修改”。\n"
            f"{extra_rules}\n\n"
            f"{follow_up_context}"
            f"任务完成判据：\n{prompt_context.completion_criteria}\n\n"
            f"当前文档上下文：\n{prompt_context.context_snapshot}\n\n"
            f"最近工具结果（结构化）：\n{prompt_context.recent_tool_results}\n\n"
            f"最近操作：\n{prompt_context.ops_summary}\n\n"
            f"可用工具：\n{prompt_context.tools_summary}\n\n"
            f"用户消息：{user_message}"
        )

    def build_presentation_intent_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        follow_up_context: str,
        user_message: str,
        template_names: str,
        extra_rules: str = "",
    ) -> str:
        """Construct the presentation-specific intent-recognition prompt."""
        return (
            "分析用户对当前演示文稿的意图，输出任务列表 JSON。\n"
            "输出格式必须是一个对象：{\"goal_status\": \"complete|need_more_steps|need_follow_up\", \"tasks\": [...]}。\n"
            "goal_status 含义：\n"
            "- complete: 原始需求已经完成；tasks 可为空，或只放 reply 任务作为最终总结。\n"
            "- need_more_steps: 原始需求还没完成；tasks 中给出下一步任务。\n"
            "- need_follow_up: 还缺关键信息；tasks 中给出 follow_up 任务。\n"
            "每个任务字段：\n"
            "- type: modify | query | follow_up | reply | confirm | cancel\n"
            "- tool_name: 需要执行的工具名，必须填写，并且必须来自下方可用工具。\n"
            "- target: 目标位置，例如幻灯片标题、页码、课堂内容稿中的某句原文。\n"
            "- action: 动作，例如 rewrite、replace_link、insert、search、locate、layout。\n"
            "- proposed_content: 给用户预览的最终改动内容摘要；若涉及改文案，这里应是最终会落到 PPT 里的内容摘要。\n"
            "- response: 当 type=reply 时，直接给用户的自然语言回答。\n"
            "- parameters: 必须是所选工具的最终参数对象，字段名、必填项和枚举值都要严格匹配工具 schema；不要依赖后端猜测或补全。\n"
            "规则：\n"
            "1. 复合指令要拆成多个任务，保持原始顺序。\n"
            "2. 用户是查询时，优先返回 query 任务并令 goal_status=need_more_steps；如果不需要工具即可回答，令 goal_status=complete 并返回 reply 任务。\n"
            "3. 用户是修改时，如果需要先定位页面、读取当前文案、确认原句或补充外部链接，可以先返回 query 任务；但同一份 tasks 里必须继续给出后续 modify 任务和最终参数，并令 goal_status=need_more_steps，不要只查不改。\n"
            "4. 当用户引用了原句、点名了页码/标题，或当前 PPT 上下文已经足够定位时，不要先追问放在哪一页；优先用 get_presentation_outline、search_in_presentation、get_slide_details 来定位。\n"
            "5. 当用户要求推荐在线 Demo、官网链接、在线可试用页面时，优先使用 search_web 搜索候选链接，再把链接和一句简要说明写入后续 modify 任务。\n"
            "5a. 如果 search_web 当前不可用或没有结果，不要把它当成整轮任务终止条件；要明确说明无法联网核验，并继续基于当前 PPT、教案上下文和模型已有知识完成能完成的设置。\n"
            "6. 若关键信息确实不足，令 goal_status=need_follow_up，并返回一个 follow_up 任务；把问题写入 parameters.question，可选按钮写入 parameters.options。\n"
            "7. 如果用户明确要确认或取消，只返回 confirm 或 cancel 任务，并令 goal_status=need_more_steps。\n"
            "8. 只使用下方可用工具中的工具名。\n"
            "9. 不要输出预设问答模板，不要复用固定答案；所有问题和回答都必须紧扣当前 PPT、最近操作和本轮用户消息。\n"
            "10. 不要把用户的操作要求原样塞进 body 或 notes。只有当该文本就是最终要显示在 PPT 上的文案时，才能写入修改工具参数。\n"
            "11. 当用户要求整体重做、拆页成多页、分步展开、跨多页重排，或要同时重写多页内容与整体结构时，优先用 replace_presentation；当用户只改某一页局部内容时，优先用 update_slide_content；当用户只是新增、删除、复制或移动少数页面时，优先用 add_slide、move_slide、delete_slide、duplicate_slide。\n"
            "11a. 只要某一步会 add_slide、delete_slide、duplicate_slide、move_slide，或任何会改变页序的操作，就不要在同一批 tasks 里继续沿用旧的 slide_index / after_slide_index / before_slide_index / new_index；纯结构调整优先用局部工具，若后续还要同时重写多页内容，再考虑 replace_presentation，或让下一轮基于最新 PPT 重新定位。\n"
            "11b. 人类说的“第 8 页/第 9 页”通常是从 1 开始数；工具参数 slide_index、after_slide_index、before_slide_index、new_index 一律从 0 开始，写参数时必须换算。\n"
            "11c. 如果 search_in_presentation 没有精确命中，不要凭猜测直接修改；必须结合模糊命中、get_slide_details 或 follow_up 再落地。\n"
            "11d. 在输出任何 PPT 修改任务前，先判断目标模板是否装得下最终内容；尤其是长代码、长段落、长列表，不要直接塞进单页。\n"
            "11e. 如果用户明确表达“分页/拆页/一页显示不完全/放不下/保留完整内容”，优先保留内容并拆成多页；如果用户明确说“精简/概览/只保留要点”，才压缩成单页。\n"
            "11f. 如果内容明显超出单页容量，而用户没有说明是要保留完整内容分页，还是改成精简版，先 follow_up 询问，不要默认精简。\n"
            "11g. 如果用户要把分页内容合并回单页、明确说“不要分页”，或搜索结果命中连续的同名分页页，必须先用 get_slide_details 读取这些页的正文，再输出最终修改；不要先把“准备合并”“先搜索后填充”这类过程说明写进 body / notes。\n"
            "12. 如果还不能可靠地产生最终写入文本，就返回 follow_up，不要用空内容调用修改工具。\n"
            "13. 如果无需任何工具且只要直接答复用户，就令 goal_status=complete 并返回 reply；如果无法可靠理解，返回 {\"goal_status\": \"need_follow_up\", \"tasks\": [follow_up]}。\n"
            "14. 如果当前消息是在回答 pending_follow_up，默认目标是继续完成上一轮任务；除非用户明确撤销或改目标，不要把补充回答重新规划成新的待确认修改。\n"
            "15. 不要为了“更完整”擅自追加用户未明确要求的衍生修改，例如重排整套 PPT、追加总结页；只有用户明确提出，或这是完成原目标不可缺少的一步时才能添加。\n"
            "16. 不要套用固定的 PPT 生产流程或预设页型需求；是否需要封面、目录、过渡页、总结页，必须由当前教案内容和用户要求决定。\n"
            "17. 是否已完成本轮用户需求，必须按下方“任务完成判据”判断；不要把“已经定位到页面/已经读到原文”误判成“已经完成修改”。\n"
            "17a. 当用户说“改成可插图/可放截图/运行结果截图/效果图占位”时，优先理解为保留或改成带图片区的页面，并把 image_description 写成用户要插入的图片类型；除非用户明确要求真实图片地址，否则不要追问 image_url。\n"
            "17b. 当用户说“移除图片占位符”“去掉这页图片”“改成纯文字版式”时，优先改成不带图片区的模板；若使用 update_slide_content，需要把 template 改成非图片模板，不要保留旧的 image_description / image_url。\n"
            f"18. 当前可用模板为：{template_names}。其中 title_subtitle 适合封面或结束页，只显示主标题和可选副标题。\n"
            f"{extra_rules}\n\n"
            f"{follow_up_context}"
            f"任务完成判据：\n{prompt_context.completion_criteria}\n\n"
            f"当前演示文稿上下文：\n{prompt_context.context_snapshot}\n\n"
            f"最近工具结果（结构化）：\n{prompt_context.recent_tool_results}\n\n"
            f"最近操作：\n{prompt_context.ops_summary}\n\n"
            f"可用工具：\n{prompt_context.tools_summary}\n\n"
            f"用户消息：{user_message}"
        )

    def build_replan_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        completed_steps: list[str] | str | None,
        user_message: str,
        extra_rules: str = "",
    ) -> str:
        """Construct the shared post-execution replanning prompt."""
        completed_steps_text = self._render_completed_steps_text(completed_steps)
        return (
            "你刚刚已经完成了一轮任务执行。现在请判断：原始用户需求是否已经完成；如果没完成，继续规划下一轮任务。\n"
            "输出格式必须是一个对象：{\"goal_status\": \"complete|need_more_steps|need_follow_up\", \"tasks\": [...]}。\n"
            "goal_status 含义：\n"
            "- complete: 原始需求已经完成；tasks 可为空，或只放 reply 任务作为最终面向用户的总结。\n"
            "- need_more_steps: 原始需求还没完成；tasks 中给出下一轮任务。\n"
            "- need_follow_up: 当前还缺关键信息；tasks 中给出 follow_up 任务。\n"
            "每个任务字段：\n"
            "- type: modify | query | follow_up | reply | confirm | cancel\n"
            "- tool_name: 需要执行的工具名，必须填写，并且必须来自下方可用工具。\n"
            "- target: 目标位置，例如章节名、幻灯片标题、页码描述。\n"
            "- action: 动作，例如 rewrite、replace、insert、layout、search。\n"
            "- proposed_content: 若涉及修改，写最终会落稿的内容摘要。\n"
            "- response: 当 type=reply 时，直接给用户的自然语言回答。\n"
            "- parameters: 必须严格匹配工具 schema。\n"
            "规则：\n"
            "1. 先严格按照下方“任务完成判据”判断原始用户需求是否已经完成。\n"
            "2. 如果原始需求已经完成，而且前面已有足够清晰的工具结果可直接作为最终回复，返回 {\"goal_status\": \"complete\", \"tasks\": []}。\n"
            "3. 如果原始需求已经完成，但还需要一条更自然的面向用户总结，返回 {\"goal_status\": \"complete\", \"tasks\": [reply]}。\n"
            "4. 如果原始需求还没完成，就返回 goal_status=need_more_steps 并继续规划下一轮任务；不要因为已经做过一次查询就停止。\n"
            "5. 不要把刚刚已经完成的同一查询原样再做一遍，除非确实需要新的补充查询。\n"
            "6. 如果当前结果仍不足以可靠落地，就令 goal_status=need_follow_up，并输出 follow_up 任务，明确说明还缺什么。\n"
            "7. 如果需要修改，就令 goal_status=need_more_steps 并直接输出 modify 任务，不要只停留在 query。\n"
            "8. 优先参考“最近工具结果（结构化）”中的事实判断是否完成，不要只凭自然语言摘要猜测。\n"
            f"{extra_rules}\n\n"
            f"任务完成判据：\n{prompt_context.completion_criteria}\n\n"
            f"当前文档上下文：\n{prompt_context.context_snapshot}\n\n"
            f"最近工具结果（结构化）：\n{prompt_context.recent_tool_results}\n\n"
            f"刚完成的步骤：\n{completed_steps_text or '无'}\n\n"
            f"最近操作：\n{prompt_context.ops_summary}\n\n"
            f"可用工具：\n{prompt_context.tools_summary}\n\n"
            f"原始用户消息：{user_message}"
        )

    def build_presentation_replan_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        completed_steps: list[str] | str | None,
        user_message: str,
        extra_rules: str = "",
    ) -> str:
        """Construct the presentation-specific post-execution replanning prompt."""
        completed_steps_text = self._render_completed_steps_text(completed_steps)
        return (
            "你刚刚已经完成了一轮 PPT 任务执行。现在请判断：原始用户需求是否已经完成；如果没完成，继续规划下一轮任务。\n"
            "输出格式必须是一个对象：{\"goal_status\": \"complete|need_more_steps|need_follow_up\", \"tasks\": [...]}。\n"
            "goal_status 含义：\n"
            "- complete: 原始需求已经完成；tasks 可为空，或只放 reply 任务作为最终面向用户的总结。\n"
            "- need_more_steps: 原始需求还没完成；tasks 中给出下一轮任务。\n"
            "- need_follow_up: 当前还缺关键信息；tasks 中给出 follow_up 任务。\n"
            "每个任务字段：\n"
            "- type: modify | query | follow_up | reply | confirm | cancel\n"
            "- tool_name: 需要执行的工具名，必须填写，并且必须来自下方可用工具。\n"
            "- target: 目标位置，例如幻灯片标题、页码、课堂内容稿中的某句原文。\n"
            "- action: 动作，例如 rewrite、replace_link、insert、search、locate、layout。\n"
            "- proposed_content: 若涉及修改，写最终会落到 PPT 里的内容摘要。\n"
            "- response: 当 type=reply 时，直接给用户的自然语言回答。\n"
            "- parameters: 必须严格匹配工具 schema。\n"
            "规则：\n"
            "1. 先严格按照下方“任务完成判据”判断原始用户需求是否已经完成。\n"
            "2. 如果原始需求已经完成，而且前面已有足够清晰的工具结果可直接作为最终回复，返回 {\"goal_status\": \"complete\", \"tasks\": []}。\n"
            "3. 如果原始需求已经完成，但还需要一条更自然的面向用户总结，返回 {\"goal_status\": \"complete\", \"tasks\": [reply]}。\n"
            "4. 如果原始需求还没完成，就返回 goal_status=need_more_steps 并继续规划下一轮任务；不要因为已经做过一次定位或查询就停止。\n"
            "5. 不要把刚刚已经完成的同一查询原样再做一遍，除非确实需要新的补充查询。\n"
            "6. 如果当前结果仍不足以可靠落地，就令 goal_status=need_follow_up，并输出 follow_up 任务，明确说明还缺什么。\n"
            "7. 如果需要修改，就令 goal_status=need_more_steps 并直接输出 modify 任务，不要只停留在 query。\n"
            "8. 优先参考“最近工具结果（结构化）”中的事实判断是否完成，不要只凭自然语言摘要猜测。\n"
            "9. 如果下一步会 add_slide、delete_slide、duplicate_slide、move_slide 或改变页序，不要继续沿用旧的 slide_index / after_slide_index / before_slide_index / new_index；纯结构调整优先继续使用局部工具，并让下一轮基于最新 PPT 重新定位；只有同时要重写多页内容与整体结构时才改用 replace_presentation。\n"
            "10. 如果当前只是完成了定位页面、读取原文、找到候选链接，这通常不算完成；只有必要修改已经被规划并准备执行，或已实际执行完成，才算完成。\n"
            "11. 如果后续修改内容明显装不进单页，而用户还没明确要分页还是精简，先输出 follow_up 澄清，不要默认精简。\n"
            f"{extra_rules}\n\n"
            f"任务完成判据：\n{prompt_context.completion_criteria}\n\n"
            f"当前演示文稿上下文：\n{prompt_context.context_snapshot}\n\n"
            f"最近工具结果（结构化）：\n{prompt_context.recent_tool_results}\n\n"
            f"刚完成的步骤：\n{completed_steps_text}\n\n"
            f"最近操作：\n{prompt_context.ops_summary}\n\n"
            f"可用工具：\n{prompt_context.tools_summary}\n\n"
            f"原始用户消息：{user_message}"
        )

    def _render_completed_steps_text(self, completed_steps: list[str] | str | None) -> str:
        """Render completed steps into the prompt-friendly bullet list."""
        if isinstance(completed_steps, str):
            text = completed_steps.strip()
            return text or "无"

        if not isinstance(completed_steps, list):
            return "无"

        lines = [
            f"- {self.truncate_text(str(item).strip(), 200)}"
            for item in completed_steps
            if str(item).strip()
        ]
        return "\n".join(lines) or "无"

    async def request_task_plan(
        self,
        *,
        create_completion: Callable[..., Awaitable[Any]],
        model: str,
        system_message: str,
        prompt: str,
    ) -> TaskList | None:
        """Call the planner LLM and parse its JSON task plan."""
        response = await create_completion(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            stream=False,
        )
        message = response.choices[0].message
        content = message.content or ""
        return self.parse_task_plan_from_llm_output(content)

    def build_follow_up_payload(self, task: Task) -> dict[str, Any]:
        """Build a structured follow-up payload from a task."""
        parameters = deepcopy(task.parameters or {})
        question = str(parameters.get("question") or task.response or task.target or "").strip()
        options = parameters.get("options")
        normalized_options = options if isinstance(options, list) else None
        return {
            "type": "follow_up",
            "question": question or "请补充更具体的修改要求。",
            "options": normalized_options,
        }

    def decorate_follow_up_payload(
        self,
        follow_up: dict[str, Any],
        *,
        previous_user_message: str,
        root_user_message: str | None = None,
        completed_steps: list[str],
        remaining_tasks: list[Task],
    ) -> dict[str, Any]:
        """Attach resumable task context to a follow-up payload."""
        payload = deepcopy(self.json_ready(follow_up))
        previous = previous_user_message.strip()
        root = str(root_user_message or previous_user_message).strip()
        if previous:
            payload["previous_user_message"] = previous
        if root:
            payload["root_user_message"] = root
        if completed_steps:
            payload["completed_steps"] = [
                self.truncate_text(str(item).strip(), 200)
                for item in completed_steps
                if str(item).strip()
            ]
        if remaining_tasks:
            payload["remaining_tasks"] = [task.model_dump() for task in remaining_tasks]
        return payload

    def build_follow_up_payload_from_task_plan(
        self,
        task_plan: TaskList,
        *,
        fallback_question: str,
    ) -> dict[str, Any]:
        """Prefer the planner-authored follow-up task, but keep a safe fallback."""
        for task in task_plan.tasks:
            if task.type == "follow_up":
                return self.build_follow_up_payload(task)
        return {
            "type": "follow_up",
            "question": fallback_question,
            "options": None,
        }

    def build_ambiguous_intent_follow_up(
        self,
        *,
        focus_labels: list[str],
        focus_kind: str,
        pending_follow_up: dict[str, Any] | None,
        pending_question_template: str,
        initial_question_template: str,
    ) -> dict[str, Any]:
        """Build a clarification follow-up when planner output is empty or unstable."""
        focus_text = "、".join(label for label in focus_labels if label)
        focus_sentence = f'当前最相关的{focus_kind}看起来是“{focus_text}”。' if focus_text else ""
        question = (
            pending_question_template if pending_follow_up and pending_follow_up.get("question") else initial_question_template
        ).format(focus_sentence=focus_sentence)
        return {
            "type": "follow_up",
            "question": question,
            "options": None,
        }

    def parse_task_plan_from_llm_output(self, content: str) -> TaskList | None:
        """Parse the LLM JSON output into a validated task plan with explicit status."""
        raw_payload = self.extract_json_payload(content)
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None

        if isinstance(parsed, list):
            payload = {"tasks": parsed}
        elif isinstance(parsed, dict):
            payload = parsed
        else:
            return None

        try:
            return TaskList.model_validate(payload)
        except Exception:  # noqa: BLE001
            return None

    def extract_json_payload(self, content: str) -> str:
        """Extract a JSON object or array from a raw LLM response."""
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()

        for opening, closing in (("{", "}"), ("[", "]")):
            start = stripped.find(opening)
            end = stripped.rfind(closing)
            if start != -1 and end != -1 and end >= start:
                return stripped[start : end + 1]
        return stripped
