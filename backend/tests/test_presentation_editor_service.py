from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.schemas import Task
from backend.app.services.presentation_editor_service import PresentationEditor


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class FakeTool:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.args_schema = EmptyArgs


class FakeToolsRegistry:
    def __init__(self) -> None:
        self.tools = {
            "get_presentation_outline": FakeTool("get_presentation_outline", "读取 PPT 大纲"),
            "search_in_presentation": FakeTool("search_in_presentation", "搜索 PPT 内容"),
            "get_slide_details": FakeTool("get_slide_details", "读取单页详情"),
            "add_slide": FakeTool("add_slide", "新增页面"),
            "duplicate_slide": FakeTool("duplicate_slide", "复制页面"),
            "move_slide": FakeTool("move_slide", "移动页面"),
            "update_slide_content": FakeTool("update_slide_content", "修改单页内容"),
            "change_layout": FakeTool("change_layout", "修改页面版式"),
            "delete_slide": FakeTool("delete_slide", "删除页面"),
            "replace_presentation": FakeTool("replace_presentation", "整体替换 PPT"),
            "search_web": FakeTool("search_web", "搜索外部网页"),
            "request_confirmation": FakeTool("request_confirmation", "请求确认"),
        }

    def list_tools(self) -> list[FakeTool]:
        return list(self.tools.values())

    def get_tool(self, name: str) -> FakeTool:
        return self.tools[name]


class FakeConversationService:
    def __init__(self, conversation: SimpleNamespace) -> None:
        self.conversation = conversation

    def get_temp_preferences(self, conversation_id: str) -> dict[str, Any]:
        if self.conversation.id != conversation_id:
            return {}
        metadata = getattr(self.conversation, "metadata_json", {}) or {}
        temp_preferences = metadata.get("temp_preferences")
        return temp_preferences if isinstance(temp_preferences, dict) else {}


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("LLM response queue is empty.")
        content = self.responses.pop(0)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class PresentationEditorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = SimpleNamespace(
            id="plan-1",
            title="AI Agent 入门",
            doc_type="presentation",
            subject="信息技术",
            grade="高一",
            content={
                "title": "AI Agent 入门",
                "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                "slides": [
                    {
                        "title": "课程封面",
                        "template": "title_body",
                        "body": "认识什么是 AI Agent",
                    },
                    {
                        "title": "课程目标与成果预览",
                        "template": "title_body",
                        "body": "一个能回答天气或进行计算的AI助手。",
                        "notes": "这里最好换成在线 Demo 链接。",
                    },
                ],
            },
        )
        self.conversation = SimpleNamespace(
            id="conv-1",
            summary=None,
            metadata_json={},
        )
        self.plan_service = SimpleNamespace(get=lambda _plan_id: self.plan)
        self.editor = PresentationEditor(
            plan_id=self.plan.id,
            conversation_id=self.conversation.id,
            plan_service=self.plan_service,
            conv_service=FakeConversationService(self.conversation),
            op_service=SimpleNamespace(),
            tools_registry=FakeToolsRegistry(),
            tool_executor=SimpleNamespace(),
            db=SimpleNamespace(),
            db_factory=None,
            llm_client=None,
        )

    def test_context_snapshot_is_slide_centric(self) -> None:
        snapshot = asyncio.run(
            self.editor._compose_context_snapshot(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="把“一个能回答天气或进行计算的AI助手。”改成在线可试用 Demo 链接",
            )
        )

        self.assertIn("幻灯片数：2", snapshot)
        self.assertIn("课程目标与成果预览", snapshot)
        self.assertIn("一个能回答天气或进行计算的AI助手", snapshot)
        self.assertNotIn("当前教案没有可用章节", snapshot)

    def test_intent_prompt_mentions_presentation_lookup_tools(self) -> None:
        prompt = asyncio.run(
            self.editor._build_intent_prompt(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="推荐一个在线可试用 Demo 链接并插到那句话的位置",
            )
        )

        self.assertIn("幻灯片标题、页码", prompt)
        self.assertIn("\"goal_status\": \"complete|need_more_steps|need_follow_up\"", prompt)
        self.assertIn("get_presentation_outline", prompt)
        self.assertIn("search_in_presentation", prompt)
        self.assertIn("search_web", prompt)
        self.assertIn("title_subtitle", prompt)
        self.assertIn("不要先追问放在哪一页", prompt)
        self.assertIn("不要套用固定的 PPT 生产流程", prompt)
        self.assertIn("无法联网核验", prompt)
        self.assertIn("slide_index、after_slide_index、before_slide_index、new_index 一律从 0 开始", prompt)
        self.assertIn("不要在同一批 tasks 里继续沿用旧的 slide_index / after_slide_index / before_slide_index / new_index", prompt)
        self.assertIn("当用户只是新增、删除、复制或移动少数页面时", prompt)
        self.assertIn("运行结果截图", prompt)
        self.assertIn("移除图片占位符", prompt)
        self.assertIn("目标模板是否装得下最终内容", prompt)
        self.assertIn("不要默认精简", prompt)
        self.assertIn("不要先把“准备合并”“先搜索后填充”这类过程说明写进", prompt)
        self.assertIn("优先在目标 slide 上写 `game_index`", prompt)
        self.assertIn("不要自由生成 `link_text`、`link_url`、真实游戏 URL", prompt)
        self.assertIn("`[[GAME_LINK:1]]`", prompt)

    def test_system_prompt_renders_structured_temp_preferences(self) -> None:
        self.conversation.metadata_json = {
            "temp_preferences": {
                "visual_focus": "visual_first",
                "language_style": "conversational",
                "other_notes": "适合时保留截图占位。",
            }
        }
        self.editor._get_active_preferences_text = lambda _user_id: ""

        prompt = asyncio.run(
            self.editor._build_system_prompt(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="把第二页改得更像真实课堂展示",
            )
        )

        self.assertIn("本次会话请额外遵循以下偏好", prompt)
        self.assertIn("更自然口语化", prompt)
        self.assertIn("优先考虑图片、案例图、示意图或截图占位", prompt)
        self.assertIn("适合时保留截图占位。", prompt)
        self.assertIn("优先写 `game_index=1/2/3...`", prompt)
        self.assertIn("不要自由生成小游戏 `link_text`、`link_url`、真实 URL", prompt)
        self.assertIn("`[[GAME_LINK:1]]`", prompt)

    def test_recognize_intent_uses_presentation_specific_system_message(self) -> None:
        self.editor.llm_client = FakeLLMClient([json.dumps({"goal_status": "complete", "tasks": []}, ensure_ascii=False)])

        asyncio.run(
            self.editor._recognize_intent(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="推荐一个在线可试用 Demo 链接并插到那句话的位置",
            )
        )

        system_message = self.editor.llm_client.chat.completions.calls[-1]["messages"][0]["content"]
        self.assertIn("演示文稿编辑器的意图识别模块", system_message)

    def test_intent_prompt_includes_completion_criteria_and_structured_tool_results(self) -> None:
        recent_ops = [
            SimpleNamespace(
                tool_name="search_in_presentation",
                arguments={"keyword": "课程目标"},
                result={
                    "ok": True,
                    "message": "找到 1 处与“课程目标”相关的 PPT 内容。",
                    "matches": [
                        {
                            "slide_index": 1,
                            "title": "课程目标与成果预览",
                            "field": "title",
                            "snippet": "课程目标与成果预览",
                        }
                    ],
                },
            )
        ]
        prompt = asyncio.run(
            self.editor._build_intent_prompt(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=recent_ops,
                user_message="把课程目标这一页拆成两页",
            )
        )

        self.assertIn("任务完成判据", prompt)
        self.assertIn("最近工具结果（结构化）", prompt)
        self.assertIn("\"tool_name\": \"search_in_presentation\"", prompt)
        self.assertIn("\"matches_count\": 1", prompt)
        self.assertIn("不要把“已经定位到页面/已经读到原文”误判成“已经完成修改”", prompt)
        self.assertIn("优先用 replace_presentation", prompt)

    def test_replan_uses_presentation_specific_prompt_and_system_message(self) -> None:
        self.editor.llm_client = FakeLLMClient([json.dumps({"goal_status": "complete", "tasks": []}, ensure_ascii=False)])
        recent_ops = [
            SimpleNamespace(
                tool_name="search_in_presentation",
                arguments={"keyword": "课程目标"},
                result={
                    "ok": True,
                    "message": "找到 1 处与“课程目标”相关的 PPT 内容。",
                    "matches": [
                        {
                            "slide_index": 1,
                            "title": "课程目标与成果预览",
                            "field": "title",
                            "snippet": "课程目标与成果预览",
                        }
                    ],
                },
            )
        ]

        asyncio.run(
            self.editor._plan_next_round_after_execution(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=recent_ops,
                user_message="把课程目标这一页拆成两页",
                completed_steps=["已定位课程目标页。"],
            )
        )

        call = self.editor.llm_client.chat.completions.calls[-1]
        prompt = call["messages"][1]["content"]
        system_message = call["messages"][0]["content"]

        self.assertIn("演示文稿编辑器的继续规划模块", system_message)
        self.assertIn("你刚刚已经完成了一轮 PPT 任务执行", prompt)
        self.assertIn("当前演示文稿上下文", prompt)
        self.assertIn("不要继续沿用旧的 slide_index / after_slide_index / before_slide_index / new_index", prompt)
        self.assertIn("如果当前只是完成了定位页面、读取原文、找到候选链接，这通常不算完成", prompt)
        self.assertIn("优先在目标 slide 上写 `game_index`", prompt)
        self.assertIn("不要自由生成 `link_text`、`link_url`、真实游戏 URL", prompt)

    def test_intent_failure_follow_up_uses_slide_language(self) -> None:
        payload = self.editor._build_intent_failure_follow_up(
            plan=self.plan,
            conversation=self.conversation,
            recent_ops=[],
            user_message="改掉“一个能回答天气或进行计算的AI助手。”",
        )

        self.assertEqual(payload["type"], "follow_up")
        self.assertIn("第 2 页《课程目标与成果预览》", payload["question"])
        self.assertNotIn("章节", payload["question"])

    def test_presentation_search_result_description_does_not_repeat_unknown_locations(self) -> None:
        task = Task(type="query", tool_name="search_in_presentation", action="search", parameters={"keyword": "分步详解"})
        result = {
            "ok": True,
            "message": (
                "找到 2 处与“分步详解”相关的 PPT 内容：\n\n"
                "1. 第 8 页《分步详解（1/2）》 | 字段：title\n"
                "   片段：分步详解（1/2）\n\n"
                "2. 第 9 页《分步详解（2/2）》 | 字段：title\n"
                "   片段：分步详解（2/2）"
            ),
            "matches": [
                {"slide_index": 7, "title": "分步详解（1/2）", "field": "title", "snippet": "分步详解（1/2）"},
                {"slide_index": 8, "title": "分步详解（2/2）", "field": "title", "snippet": "分步详解（2/2）"},
            ],
        }

        description = self.editor._describe_task_result(task, result)

        self.assertIn("第 8 页《分步详解（1/2）》", description)
        self.assertNotIn("未知位置", description)
        self.assertEqual(description.count("第 8 页《分步详解（1/2）》"), 1)

    def test_validate_task_queue_blocks_reorder_then_fixed_index_updates(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="第一步：导入模块",
                parameters={"template": "title_body", "title": "第一步：导入模块", "after_slide_index": 8},
            ),
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="第二步：初始化LLM",
                parameters={"slide_index": 8, "title": "第二步：初始化LLM", "body": "load_dotenv()"},
            ),
        ]

        follow_up = self.editor._validate_task_queue(tasks)

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("会改变页序", follow_up["question"])
        self.assertIn("replace_presentation", follow_up["question"])
        self.assertEqual(follow_up["options"], ["整体重排", "只做第一步", "取消这批修改"])

    def test_process_task_queue_preserves_blocked_tasks_for_confirmation(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="delete_slide",
                action="delete",
                target="第 2 页",
                parameters={"slide_index": 1},
            )
        ]

        _, _, remaining_tasks, follow_up, confirmation = asyncio.run(
            self.editor._process_task_queue(
                self.conversation.id,
                tasks,
                modify_execution_budget=0,
                quality_context={"request_text": "删除第 2 页"},
            )
        )

        self.assertIsNone(follow_up)
        self.assertIsNotNone(confirmation)
        assert confirmation is not None
        self.assertEqual(len(remaining_tasks), 1)
        self.assertEqual(remaining_tasks[0].tool_name, "delete_slide")
        self.assertEqual(confirmation["tool_to_confirm"], "delete_slide")

    def test_resolve_guard_follow_up_reply_keeps_only_first_task(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="第一步：导入模块",
                parameters={"template": "title_body", "title": "第一步：导入模块", "after_slide_index": 8},
            ),
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="第二步：初始化LLM",
                parameters={"slide_index": 8, "title": "第二步：初始化LLM", "body": "load_dotenv()"},
            ),
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "只做第一步",
            {
                "follow_up_kind": "slide_reorder_conflict",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_more_steps")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "add_slide")

    def test_resolve_guard_follow_up_reply_resumes_paginate_choice_without_llm_replan(self) -> None:
        long_body = "\n".join(f"line {index}: print('agent step {index}')" for index in range(1, 30))
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={
                    "slide_index": 1,
                    "title": "第四部分：完整示例代码",
                    "body": long_body,
                    "template": "title_body",
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "自动分页保留完整内容",
            {
                "follow_up_kind": "slide_overflow_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_more_steps")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "update_slide_content")

    def test_resolve_guard_follow_up_reply_condenses_overflowing_slide_without_llm_replan(self) -> None:
        long_body = "\n".join(f"line {index}: print('agent step {index}')" for index in range(1, 30))
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={
                    "slide_index": 1,
                    "title": "第四部分：完整示例代码",
                    "body": long_body,
                    "template": "title_body",
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "改成单页精简版",
            {
                "follow_up_kind": "slide_overflow_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_more_steps")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "update_slide_content")
        self.assertNotEqual(task_plan.tasks[0].parameters["body"], long_body)
        self.assertIsNone(self.editor._find_slide_overflow_issue(task_plan.tasks[0]))

    def test_resolve_guard_follow_up_reply_rewrites_scope_guard_to_local_updates(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="只改第二页",
                parameters={
                    "title": "AI Agent 入门",
                    "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_body",
                            "title": "课程目标与成果预览",
                            "body": "换成在线可试用 Demo 链接。",
                            "notes": "保留教师口播提示。",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "按局部修改",
            {
                "follow_up_kind": "replace_scope_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_more_steps")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "update_slide_content")
        self.assertEqual(task_plan.tasks[0].parameters["slide_index"], 1)
        self.assertEqual(task_plan.tasks[0].parameters["body"], "换成在线可试用 Demo 链接。")
        self.assertEqual(task_plan.tasks[0].parameters["notes"], "保留教师口播提示。")

    def test_resolve_guard_follow_up_reply_scope_guard_does_not_clear_omitted_optional_fields(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="只改第二页正文",
                parameters={
                    "title": "AI Agent 入门",
                    "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_body",
                            "title": "课程目标与成果预览",
                            "body": "只替换正文，不动备注。",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "按局部修改",
            {
                "follow_up_kind": "replace_scope_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "update_slide_content")
        self.assertEqual(task_plan.tasks[0].parameters["body"], "只替换正文，不动备注。")
        self.assertNotIn("notes", task_plan.tasks[0].parameters)

    def test_resolve_guard_follow_up_reply_scope_guard_maps_title_subtitle_body_to_subtitle(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="把第二页改成封面样式",
                parameters={
                    "title": "AI Agent 入门",
                    "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_subtitle",
                            "title": "课程目标与成果预览",
                            "body": "新的封面副标题",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "按局部修改",
            {
                "follow_up_kind": "replace_scope_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "update_slide_content")
        self.assertEqual(task_plan.tasks[0].parameters["slide_index"], 1)
        self.assertEqual(task_plan.tasks[0].parameters["template"], "title_subtitle")
        self.assertEqual(task_plan.tasks[0].parameters["subtitle"], "新的封面副标题")
        self.assertNotIn("body", task_plan.tasks[0].parameters)

    def test_resolve_guard_follow_up_reply_preserves_current_content_for_replace_content_guard(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="调整第二页",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_body",
                            "title": "课程目标与成果预览",
                            "body": "两句话……",
                            "notes": "待补充",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "保留其余页面原内容",
            {
                "follow_up_kind": "replace_content_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_more_steps")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].tool_name, "replace_presentation")
        repaired_slides = task_plan.tasks[0].parameters["slides"]
        self.assertEqual(repaired_slides[1]["body"], "一个能回答天气或进行计算的AI助手。")
        self.assertEqual(repaired_slides[1]["notes"], "这里最好换成在线 Demo 链接。")
        self.assertIsNone(
            self.editor._find_replace_presentation_placeholder_issue(
                "replace_presentation",
                task_plan.tasks[0].parameters,
            )
        )

    def test_resolve_guard_follow_up_reply_replace_content_guard_preserves_omitted_fields(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="调整第二页正文",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_body",
                            "title": "课程目标与成果预览",
                            "body": "新的正文内容。",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "保留其余页面原内容",
            {
                "follow_up_kind": "replace_content_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        repaired_slides = task_plan.tasks[0].parameters["slides"]
        self.assertEqual(repaired_slides[1]["body"], "新的正文内容。")
        self.assertEqual(repaired_slides[1]["notes"], "这里最好换成在线 Demo 链接。")

    def test_resolve_guard_follow_up_reply_replace_content_guard_preserves_title_subtitle_body_placeholder(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="把第二页改成封面样式",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {
                            "template": "title_subtitle",
                            "title": "课程目标与成果预览",
                            "body": "两句话……",
                        },
                    ],
                },
            )
        ]

        task_plan = self.editor._resolve_guard_follow_up_reply(
            "保留其余页面原内容",
            {
                "follow_up_kind": "replace_content_guard",
                "remaining_tasks": [task.model_dump() for task in tasks],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        repaired_slides = task_plan.tasks[0].parameters["slides"]
        self.assertEqual(repaired_slides[1]["template"], "title_subtitle")
        self.assertEqual(repaired_slides[1]["subtitle"], "一个能回答天气或进行计算的AI助手。")
        self.assertEqual(repaired_slides[1]["body"], "")

    def test_resolve_guard_follow_up_reply_empty_replace_guard_requires_full_rewrite_payload(self) -> None:
        task_plan = self.editor._resolve_guard_follow_up_reply(
            "整体重排",
            {
                "follow_up_kind": "empty_replace_guard",
                "remaining_tasks": [
                    Task(
                        type="modify",
                        tool_name="replace_presentation",
                        action="layout",
                        target="整体重排",
                        parameters={"title": "AI Agent 入门", "slides": []},
                    ).model_dump()
                ],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_follow_up")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].type, "follow_up")
        self.assertIn("完整的页级结果", task_plan.tasks[0].parameters["question"])
        self.assertEqual(task_plan.tasks[0].parameters["options"], ["我来补完整页级内容", "取消这次修改"])

    def test_resolve_guard_follow_up_reply_empty_replace_guard_requests_page_level_details_for_local_mode(self) -> None:
        task_plan = self.editor._resolve_guard_follow_up_reply(
            "按局部修改",
            {
                "follow_up_kind": "empty_replace_guard",
                "previous_user_message": "把最后一页移到第一页",
                "remaining_tasks": [
                    Task(
                        type="modify",
                        tool_name="replace_presentation",
                        action="layout",
                        target="把最后一页移到第一页",
                        parameters={"title": "AI Agent 入门", "slides": []},
                    ).model_dump()
                ],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_follow_up")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].type, "follow_up")
        self.assertIn("把最后一页移到第一页", task_plan.tasks[0].parameters["question"])
        self.assertIn("页级 payload", task_plan.tasks[0].parameters["question"])
        self.assertEqual(task_plan.tasks[0].parameters["options"], ["补充具体页级修改", "取消这次修改"])

    def test_validate_task_queue_allows_append_only_sequence(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="补充页 1",
                parameters={"template": "title_body", "title": "补充页 1", "after_slide_index": -1},
            ),
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="补充页 2",
                parameters={"template": "title_body", "title": "补充页 2", "after_slide_index": -1},
            ),
        ]

        follow_up = self.editor._validate_task_queue(tasks)

        self.assertIsNone(follow_up)

    def test_pending_follow_up_resumes_without_resetting_confirmation_budget(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={"slide_index": 1, "body": "换成在线可试用 Demo 链接。"},
            )
        ]

        budget = self.editor._get_initial_modify_execution_budget(
            tasks,
            pending_follow_up={"question": "你想改哪一页？"},
        )

        self.assertIsNone(budget)

    def test_validate_task_queue_blocks_replace_presentation_for_structural_local_request(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="删除一页并新增一页",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {"template": "title_body", "title": "在线 Demo", "body": "保留课程目标页，并新增一个可在线试用的天气助手链接。"},
                        {"template": "title_body", "title": "新增页", "body": "Demo 地址：https://example.com/agent-demo，课堂上演示输入城市即可返回天气。"},
                    ],
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={"request_text": "删除第 2 页，再新增一页放 demo 链接"},
        )

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["follow_up_kind"], "replace_scope_guard")
        self.assertIn("局部改页", follow_up["question"])
        self.assertEqual(follow_up["options"], ["按局部修改", "整体重排", "取消这次修改"])

    def test_validate_task_queue_blocks_replace_presentation_with_placeholder_rewrite_text(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="删除一页并新增一页",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                        {"template": "title_body", "title": "改写后的第 2 页", "body": "两句话……"},
                        {"template": "title_body", "title": "新增页", "body": "待补充"},
                    ],
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={"request_text": "删除第 2 页，再新增一页放 demo 链接"},
        )

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["follow_up_kind"], "replace_content_guard")
        self.assertIn("整体替换", follow_up["question"])
        self.assertIn("两句话……", follow_up["question"])
        self.assertEqual(follow_up["options"], ["保留其余页面原内容", "我会提供完整重排文案", "取消这次修改"])

    def test_intent_prompt_includes_guard_resume_hint_for_scope_follow_up(self) -> None:
        prompt = asyncio.run(
            self.editor._build_intent_prompt(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="按局部修改",
                pending_follow_up={
                    "question": "这次看起来更像局部改页。",
                    "options": ["按局部修改", "整体重排", "取消这次修改"],
                    "follow_up_kind": "replace_scope_guard",
                    "previous_user_message": "删除第 2 页，再新增一页放 demo 链接",
                    "remaining_tasks": [
                        Task(
                            type="modify",
                            tool_name="replace_presentation",
                            action="layout",
                            target="删除一页并新增一页",
                            parameters={
                                "title": "AI Agent 入门",
                                "slides": [
                                    {"template": "title_body", "title": "课程封面", "body": "认识什么是 AI Agent"},
                                    {"template": "title_body", "title": "改写后的第 2 页", "body": "两句话……"},
                                ],
                            },
                        ).model_dump()
                    ],
                },
            )
        )

        self.assertIn("如果用户回复“按局部修改”", prompt)
        self.assertIn("不要再次直接输出 replace_presentation", prompt)

    def test_validate_task_queue_blocks_empty_replace_presentation(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="把最后一页移到第一页",
                parameters={
                    "title": "AI Agent 入门",
                    "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                    "slides": [],
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={"request_text": "把最后一页幻灯片改为第一页"},
        )

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("replace_presentation", follow_up["question"])
        self.assertIn("只有 0 页", follow_up["question"])
        self.assertIn("清空", follow_up["question"])
        self.assertEqual(follow_up["options"], ["按局部修改", "整体重排", "取消这次修改"])

    def test_validate_task_queue_allows_replace_presentation_for_global_rewrite_request(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="整体重排",
                parameters={
                    "title": "AI Agent 入门",
                    "slides": [
                        {"template": "title_body", "title": "封面", "body": "新结构"},
                        {"template": "title_body", "title": "目录", "body": "重排后的结构"},
                    ],
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={"request_text": "整体重排这份 PPT，按新的叙事重做全部页面"},
        )

        self.assertIsNone(follow_up)

    def test_rewrite_task_queue_auto_paginate_overflowing_update_when_requested(self) -> None:
        long_body = "\n".join(f"line {index}: print('agent step {index}')" for index in range(1, 30))
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={
                    "slide_index": 1,
                    "title": "第四部分：完整示例代码",
                    "body": long_body,
                    "template": "title_body",
                },
            )
        ]

        rewritten = asyncio.run(
            self.editor._rewrite_task_queue(
                tasks,
                quality_context={"prefers_paginate": True, "prefers_condense": False},
            )
        )

        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0].tool_name, "replace_presentation")
        self.assertEqual(rewritten[0].parameters["_auto_strategy"], "paginate_overflow")
        self.assertGreaterEqual(len(rewritten[0].parameters["slides"]), 3)
        self.assertIn("（1/", rewritten[0].parameters["slides"][1]["title"])

    def test_rewrite_task_queue_defaults_cover_addition_to_front(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="新增一页封面",
                parameters={"template": "title_subtitle", "title": "AI Agent 入门", "subtitle": "课程导入"},
            )
        ]

        rewritten = asyncio.run(
            self.editor._rewrite_task_queue(
                tasks,
                quality_context={"request_text": "新增一页封面"},
            )
        )

        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0].tool_name, "add_slide")
        self.assertEqual(rewritten[0].parameters["before_slide_index"], 0)
        self.assertNotIn("after_slide_index", rewritten[0].parameters)

    def test_rewrite_task_queue_converts_pure_reorder_replace_to_move_slide(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="replace_presentation",
                action="layout",
                target="把最后一页移到第一页",
                parameters={
                    "title": "AI Agent 入门",
                    "classroom_script": "先看目标，再展示一个在线可试用 Demo。",
                    "slides": [
                        {
                            "title": "课程目标与成果预览",
                            "template": "title_body",
                            "body": "一个能回答天气或进行计算的AI助手。",
                            "notes": "这里最好换成在线 Demo 链接。",
                        },
                        {
                            "title": "课程封面",
                            "template": "title_body",
                            "body": "认识什么是 AI Agent",
                        },
                    ],
                },
            )
        ]

        rewritten = asyncio.run(
            self.editor._rewrite_task_queue(
                tasks,
                quality_context={"request_text": "把最后一页移到第一页"},
            )
        )

        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0].tool_name, "move_slide")
        self.assertEqual(rewritten[0].parameters["slide_index"], 1)
        self.assertEqual(rewritten[0].parameters["new_index"], 0)

    def test_rewrite_task_queue_paginated_cover_insert_keeps_front_position(self) -> None:
        long_subtitle = "\n".join(f"课程导入第 {index} 点：说明完整背景和目标" for index in range(1, 20))
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="新增一页封面",
                parameters={"template": "title_subtitle", "title": "AI Agent 入门", "subtitle": long_subtitle},
            )
        ]

        rewritten = asyncio.run(
            self.editor._rewrite_task_queue(
                tasks,
                quality_context={"request_text": "新增一页封面并保留完整内容", "prefers_paginate": True, "prefers_condense": False},
            )
        )

        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0].tool_name, "replace_presentation")
        self.assertEqual(rewritten[0].parameters["_auto_strategy"], "paginate_overflow")
        self.assertIn("（1/", rewritten[0].parameters["slides"][0]["title"])
        cover_index = next(
            index for index, slide in enumerate(rewritten[0].parameters["slides"]) if slide["title"] == "课程封面"
        )
        self.assertGreaterEqual(cover_index, 1)

    def test_validate_task_queue_treats_before_insert_as_fixed_position(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="add_slide",
                action="insert",
                target="封面",
                parameters={"template": "title_subtitle", "title": "封面", "before_slide_index": 0},
            ),
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程封面",
                parameters={"slide_index": 0, "body": "新的正文"},
            ),
        ]

        follow_up = self.editor._validate_task_queue(tasks)

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["follow_up_kind"], "slide_reorder_conflict")

    def test_validate_task_queue_blocks_overflowing_slide_when_user_intent_is_ambiguous(self) -> None:
        long_body = "\n".join(f"line {index}: print('agent step {index}')" for index in range(1, 30))
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={
                    "slide_index": 1,
                    "title": "第四部分：完整示例代码",
                    "body": long_body,
                    "template": "title_body",
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={
                "prefers_paginate": False,
                "prefers_condense": False,
                "prefers_keep_full_slide_content": True,
            },
        )

        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertEqual(follow_up["follow_up_kind"], "slide_overflow_guard")
        self.assertIn("完整显示", follow_up["question"])
        self.assertEqual(follow_up["options"], ["自动分页保留完整内容", "改成单页精简版", "取消这次修改"])

    def test_validate_task_queue_allows_overflowing_slide_when_user_chose_condense(self) -> None:
        long_body = "\n".join(f"line {index}: print('agent step {index}')" for index in range(1, 30))
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="课程目标与成果预览",
                parameters={
                    "slide_index": 1,
                    "title": "第四部分：完整示例代码",
                    "body": long_body,
                    "template": "title_body",
                },
            )
        ]

        follow_up = self.editor._validate_task_queue(
            tasks,
            quality_context={
                "prefers_paginate": False,
                "prefers_condense": True,
                "prefers_keep_full_slide_content": False,
            },
        )

        self.assertIsNone(follow_up)

    def test_process_task_queue_blocks_process_placeholder_slide_write_before_delete(self) -> None:
        tasks = [
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="防溺水“六不准”",
                parameters={
                    "slide_index": 1,
                    "title": "防溺水“六不准”",
                    "body": "准备将内容合并到第5页。需要先搜索确定具体内容后填充。",
                    "template": "title_body",
                },
            ),
            Task(
                type="modify",
                tool_name="delete_slide",
                action="delete",
                target="第 3 页",
                parameters={"slide_index": 1},
            ),
        ]

        tool_events, response_texts, remaining_tasks, follow_up, confirmation = asyncio.run(
            self.editor._process_task_queue(
                self.conversation.id,
                tasks,
                modify_execution_budget=1,
                quality_context={"request_text": "六不准也很短，不要分页"},
            )
        )

        self.assertEqual(tool_events, [])
        self.assertEqual(response_texts, [])
        self.assertIsNone(confirmation)
        self.assertIsNotNone(follow_up)
        assert follow_up is not None
        self.assertIn("过程说明或占位稿", follow_up["question"])
        self.assertIn("最终要显示在幻灯片上的标题/正文", follow_up["question"])
        self.assertEqual(len(remaining_tasks), 1)
        self.assertEqual(remaining_tasks[0].tool_name, "delete_slide")

    def test_recognize_intent_forces_follow_up_for_unresolved_local_slide_reference(self) -> None:
        self.editor.llm_client = FakeLLMClient([])

        task_plan = asyncio.run(
            self.editor._recognize_intent(
                plan=self.plan,
                conversation=self.conversation,
                recent_ops=[],
                user_message="这页改一下",
            )
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "need_follow_up")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].type, "follow_up")
        self.assertIn("意图还不够明确", task_plan.tasks[0].parameters["question"])

    def test_normalize_replace_presentation_coerces_nullable_slide_fields(self) -> None:
        normalized = self.editor._normalize_tool_arguments(
            "replace_presentation",
            {
                "title": "AI Agent 入门",
                "classroom_script": "先看目标，再展示结果截图。",
                "slides": [
                    {
                        "layout": "image",
                        "title": None,
                        "body": None,
                        "subtitle": None,
                        "game_index": "2",
                        "image_description": "运行结果截图",
                    }
                ],
            },
        )

        self.assertEqual(normalized["slides"][0]["template"], "image")
        self.assertEqual(normalized["slides"][0]["title"], "")
        self.assertEqual(normalized["slides"][0]["body"], "")
        self.assertIsNone(normalized["slides"][0]["subtitle"])
        self.assertEqual(normalized["slides"][0]["game_index"], 2)
        self.assertEqual(normalized["slides"][0]["image_description"], "运行结果截图")

    def test_resolve_guard_follow_up_reply_accepts_current_structure(self) -> None:
        task_plan = self.editor._resolve_guard_follow_up_reply(
            "当前10页结构正是我想要的，无需其他修改",
            {
                "follow_up_kind": "slide_reorder_conflict",
                "question": "请确认是否还要继续改。",
                "remaining_tasks": [],
            },
        )

        self.assertIsNotNone(task_plan)
        assert task_plan is not None
        self.assertEqual(task_plan.goal_status, "complete")
        self.assertEqual(len(task_plan.tasks), 1)
        self.assertEqual(task_plan.tasks[0].type, "reply")
        self.assertIn("不再继续修改", task_plan.tasks[0].response or "")

    def test_build_task_arguments_realigns_explicit_slide_numbers(self) -> None:
        arguments = self.editor._build_task_arguments(
            Task(
                type="modify",
                tool_name="update_slide_content",
                action="rewrite",
                target="把第 2 页改成新的 demo 链接页",
                parameters={"slide_index": 2, "title": "在线 Demo", "body": "新内容"},
            )
        )

        self.assertEqual(arguments["slide_index"], 1)

    def test_should_pause_for_confirmation_only_for_high_risk_ppt_modify(self) -> None:
        delete_task = Task(
            type="modify",
            tool_name="delete_slide",
            action="delete",
            target="第 2 页",
            parameters={"slide_index": 1},
        )
        layout_task = Task(
            type="modify",
            tool_name="change_layout",
            action="layout",
            target="第 2 页",
            parameters={"slide_index": 1, "new_layout": "image_text"},
        )

        delete_pause = self.editor._should_pause_for_confirmation(
            delete_task,
            tool_name="delete_slide",
            arguments=self.editor._build_task_arguments(delete_task),
            remaining_tasks=[delete_task],
            remaining_modify_budget=0,
        )
        layout_pause = self.editor._should_pause_for_confirmation(
            layout_task,
            tool_name="change_layout",
            arguments=self.editor._build_task_arguments(layout_task),
            remaining_tasks=[layout_task],
            remaining_modify_budget=0,
        )

        self.assertTrue(delete_pause)
        self.assertFalse(layout_pause)

    def test_invalid_task_follow_up_humanizes_replace_presentation_issues(self) -> None:
        task = Task(
            type="modify",
            tool_name="replace_presentation",
            action="layout",
            parameters={},
        )

        follow_up = self.editor._build_invalid_task_follow_up(
            task,
            [
                "slides.12.title: Input should be a valid string",
                "slides.12.body: Input should be a valid string",
            ],
        )

        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("整体重排这份 PPT", follow_up["question"])
        self.assertIn("第 13 页的标题", follow_up["question"])
        self.assertIn("第 13 页的正文", follow_up["question"])
        self.assertNotIn("slides.12.title", follow_up["question"])
        self.assertNotIn("Input should be a valid string", follow_up["question"])

    def test_invalid_task_follow_up_strips_value_error_prefix_for_empty_replace(self) -> None:
        task = Task(
            type="modify",
            tool_name="replace_presentation",
            action="layout",
            parameters={},
        )

        follow_up = self.editor._build_invalid_task_follow_up(
            task,
            ["Value error, slides 不能为空；整体替换演示文稿时至少提供 1 页幻灯片。"],
        )

        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("至少提供 1 页幻灯片", follow_up["question"])
        self.assertNotIn("Value error", follow_up["question"])


if __name__ == "__main__":
    unittest.main()
