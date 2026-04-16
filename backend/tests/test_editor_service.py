from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from typing import Literal

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.schemas import Task
from backend.app.services.editor_service import DocumentEditor


class RewriteSectionArgs(BaseModel):
    section_type: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=1)
    preserve_duration: bool = True


class AdjustDurationArgs(BaseModel):
    section_type: str = Field(..., min_length=1)
    new_duration: int


class InsertElementArgs(BaseModel):
    target_section: str = Field(..., min_length=1)
    position: Literal["start", "end", "after_paragraph"]
    element_type: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class SearchInPlanArgs(BaseModel):
    keyword: str = Field(..., min_length=1)


class GetSectionDetailsArgs(BaseModel):
    section_type: str | None = None
    section_index: int | None = Field(default=None, ge=0)
    include_neighbors: bool = True


class ReplaceParagraphsInSectionArgs(BaseModel):
    section_type: str | None = None
    section_index: int | None = Field(default=None, ge=0)
    start_paragraph_index: int = Field(..., ge=0)
    end_paragraph_index: int | None = Field(default=None, ge=0)
    new_text: str = Field(..., min_length=1)


class EvaluatePlanSuitabilityArgs(BaseModel):
    focus: str | None = None


class SilentQueryArgs(BaseModel):
    pass


class FakePlanService:
    def __init__(self, plan: SimpleNamespace) -> None:
        self.plan = plan

    def get(self, plan_id: str) -> SimpleNamespace | None:
        if self.plan.id == plan_id:
            return self.plan
        return None


class FakeConversationService:
    def __init__(self, conversation: SimpleNamespace) -> None:
        self.conversation = conversation

    def get(self, conversation_id: str) -> SimpleNamespace | None:
        if self.conversation.id == conversation_id:
            return self.conversation
        return None

    def create(self, plan_id: str) -> SimpleNamespace:
        self.conversation.plan_id = plan_id
        return self.conversation

    def update(self, conversation_id: str, data: Any) -> SimpleNamespace | None:
        if self.conversation.id != conversation_id:
            return None
        payload = data.model_dump(exclude_unset=True)
        metadata = payload.get("metadata")
        if metadata is not None:
            self.conversation.metadata_json = metadata
        return self.conversation

    def get_temp_preferences(self, conversation_id: str) -> dict[str, Any]:
        if self.conversation.id != conversation_id:
            return {}
        metadata = getattr(self.conversation, "metadata_json", {}) or {}
        temp_preferences = metadata.get("temp_preferences")
        return temp_preferences if isinstance(temp_preferences, dict) else {}


class FakeOperationService:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def list_by_conversation(self, conversation_id: str, limit: int = 10) -> list[Any]:
        return self.items[-limit:]

    def create(self, payload: Any) -> Any:
        record = SimpleNamespace(
            conversation_id=payload.conversation_id,
            tool_name=payload.tool_name,
            arguments=payload.arguments,
            result=payload.result,
        )
        self.items.append(record)
        return record


class FakeTool:
    def __init__(self, name: str, description: str, args_schema: type[BaseModel]) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema


class FakeToolsRegistry:
    def __init__(self) -> None:
        self.tools = {
            "rewrite_section": FakeTool("rewrite_section", "重写章节内容", RewriteSectionArgs),
            "adjust_duration": FakeTool("adjust_duration", "调整章节时长", AdjustDurationArgs),
            "insert_element": FakeTool("insert_element", "插入章节元素", InsertElementArgs),
            "search_in_plan": FakeTool("search_in_plan", "搜索教案内容", SearchInPlanArgs),
            "get_section_details": FakeTool("get_section_details", "读取章节详情", GetSectionDetailsArgs),
            "replace_paragraphs_in_section": FakeTool(
                "replace_paragraphs_in_section",
                "按段替换章节内容",
                ReplaceParagraphsInSectionArgs,
            ),
            "evaluate_plan_suitability": FakeTool(
                "evaluate_plan_suitability",
                "评估教案整体适配度",
                EvaluatePlanSuitabilityArgs,
            ),
            "silent_query": FakeTool("silent_query", "返回空结果的测试工具", SilentQueryArgs),
        }

    def list_tools(self) -> list[FakeTool]:
        return list(self.tools.values())

    def get_tool(self, name: str) -> FakeTool:
        return self.tools[name]


class FakeToolExecutor:
    def __init__(self, plan: SimpleNamespace) -> None:
        self.plan = plan
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        payload = dict(arguments or {})
        payload.update(kwargs)
        self.calls.append((tool_name, payload))

        if tool_name == "rewrite_section":
            section_type = payload["section_type"]
            for section in self.plan.content["sections"]:
                if section.get("type") == section_type:
                    section["content"] = payload["new_content"]
                    return {"ok": True, "message": f"已成功重写{section_type}章节。"}
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        if tool_name == "adjust_duration":
            section_type = payload["section_type"]
            for section in self.plan.content["sections"]:
                if section.get("type") == section_type:
                    section["duration"] = payload["new_duration"]
                    return {"ok": True, "message": f"已将{section_type}章节时长调整为{payload['new_duration']}分钟。"}
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        if tool_name == "insert_element":
            target_section = payload["target_section"]
            for section in self.plan.content["sections"]:
                if section.get("type") != target_section:
                    continue
                elements = section.setdefault("elements", [])
                new_element = {"type": payload["element_type"], "content": payload["content"]}
                if payload["position"] == "start":
                    elements.insert(0, new_element)
                else:
                    elements.append(new_element)
                section["content"] = f"{section.get('content', '')}\n\n[{payload['element_type']}] {payload['content']}".strip()
                return {"ok": True, "message": f"已在{target_section}章节插入{payload['element_type']}元素。"}
            return {"ok": False, "message": f"错误：未找到章节 {target_section}。"}

        if tool_name == "search_in_plan":
            keyword = payload["keyword"]
            matches = []
            for section in self.plan.content["sections"]:
                content = section.get("content", "")
                if keyword in content or keyword == section.get("type"):
                    matches.append(
                        {
                            "section": section.get("type", "未知章节"),
                            "snippet": content[:40],
                        }
                    )
            return {
                "ok": True,
                "message": f"找到 {len(matches)} 处与“{keyword}”相关的内容。",
                "matches": matches,
            }

        if tool_name == "get_section_details":
            section_type = payload.get("section_type")
            section_index = payload.get("section_index")
            if section_type is not None:
                for index, section in enumerate(self.plan.content["sections"]):
                    if section.get("type") != section_type:
                        continue
                    paragraphs = [
                        {"index": paragraph_index, "text": item}
                        for paragraph_index, item in enumerate(
                            [part for part in str(section.get("content") or "").split("\n\n") if part.strip()]
                        )
                    ]
                    return {
                        "ok": True,
                        "message": f"已读取章节“{section_type}”的详细上下文。",
                        "section": {
                            "section_index": index,
                            "section_type": section_type,
                            "duration": section.get("duration"),
                            "paragraphs": paragraphs,
                        },
                    }
            if isinstance(section_index, int) and 0 <= section_index < len(self.plan.content["sections"]):
                section = self.plan.content["sections"][section_index]
                return {
                    "ok": True,
                    "message": f"已读取章节“{section.get('type', '未知章节')}”的详细上下文。",
                    "section": {
                        "section_index": section_index,
                        "section_type": section.get("type", "未知章节"),
                        "duration": section.get("duration"),
                        "paragraphs": [],
                    },
                }
            return {"ok": False, "message": "错误：未找到目标章节。"}

        if tool_name == "replace_paragraphs_in_section":
            section_type = payload.get("section_type")
            for section in self.plan.content["sections"]:
                if section.get("type") != section_type:
                    continue
                paragraphs = [part for part in str(section.get("content") or "").split("\n\n") if part.strip()]
                start_index = payload["start_paragraph_index"]
                end_index = payload.get("end_paragraph_index", start_index)
                section["content"] = "\n\n".join(
                    [*paragraphs[:start_index], payload["new_text"], *paragraphs[end_index + 1 :]]
                )
                return {"ok": True, "message": f"已替换“{section_type}”中的段落。"}
            return {"ok": False, "message": f"错误：未找到章节 {section_type}。"}

        if tool_name == "evaluate_plan_suitability":
            return {
                "ok": True,
                "message": "围绕“入门适配度”的评估结果：当前教案整体难度中。基本适合作为第一课，但建议先补充更直观的导入或示例。",
                "reasons": [
                    "共 3 个章节，总时长约 30 分钟。",
                    "导入与新授衔接清晰，但还可以增加更直观的情境。",
                ],
            }

        if tool_name == "silent_query":
            return {"ok": True}

        return {"ok": False, "message": f"未知工具：{tool_name}"}


class FakeDB:
    def __init__(self, plan: SimpleNamespace, conversation: SimpleNamespace) -> None:
        self.plan = plan
        self.conversation = conversation

    def get(self, model: Any, identifier: str) -> SimpleNamespace | None:
        model_name = getattr(model, "__name__", "")
        if model_name == "Plan" and identifier == self.plan.id:
            return self.plan
        if model_name == "Conversation" and identifier == self.conversation.id:
            return self.conversation
        return None

    def refresh(self, obj: Any) -> None:
        return None


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


class DocumentEditorTaskQueueTests(unittest.IsolatedAsyncioTestCase):
    def make_editor(
        self,
        llm_responses: list[str],
        *,
        temp_preferences: dict[str, Any] | None = None,
    ) -> tuple[DocumentEditor, SimpleNamespace, SimpleNamespace]:
        plan = SimpleNamespace(
            id="plan-1",
            title="测试教案",
            doc_type="lesson",
            subject="数学",
            grade="五年级",
            content={
                "sections": [
                    {"type": "教学目标", "content": "原始教学目标", "duration": 0},
                    {"type": "导入", "content": "生活化导入示例", "duration": 5},
                    {"type": "新授", "content": "原始新授内容", "duration": 25},
                ]
            },
            metadata_json={},
        )
        conversation = SimpleNamespace(
            id="conv-1",
            plan_id=plan.id,
            metadata_json={"temp_preferences": temp_preferences or {}},
            summary=None,
            status="active",
        )

        editor = DocumentEditor(
            plan_id=plan.id,
            conversation_id=conversation.id,
            plan_service=FakePlanService(plan),
            conv_service=FakeConversationService(conversation),
            op_service=FakeOperationService(),
            tools_registry=FakeToolsRegistry(),
            tool_executor=FakeToolExecutor(plan),
            db=FakeDB(plan, conversation),
            llm_client=FakeLLMClient(llm_responses),
        )
        return editor, plan, conversation

    async def collect_events(self, editor: DocumentEditor, message: str) -> list[tuple[str, dict[str, Any]]]:
        items: list[tuple[str, dict[str, Any]]] = []
        async for raw_event in editor.process_message(message):
            lines = [line for line in raw_event.strip().splitlines() if line]
            event_name = lines[0].split(": ", 1)[1]
            payload = json.loads(lines[1].split(": ", 1)[1])
            items.append((event_name, payload))
        return items

    async def test_modify_task_requires_confirmation_then_executes(self) -> None:
        editor, plan, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "rewrite_section",
                                "target": "教学目标",
                                "action": "simplify",
                                "proposed_content": "1. 理解重点概念；2. 完成基础应用。",
                                "parameters": {
                                    "section_type": "教学目标",
                                    "new_content": "1. 理解重点概念；2. 完成基础应用。",
                                    "preserve_duration": True,
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        first_round = await self.collect_events(editor, "简化教学目标")
        confirmation_events = [payload for event, payload in first_round if event == "confirmation_required"]
        self.assertEqual(len(confirmation_events), 1)
        self.assertEqual(confirmation_events[0]["tool_to_confirm"], "rewrite_section")
        self.assertEqual(len(conversation.metadata_json.get("pending_tasks", [])), 1)

        second_round = await self.collect_events(editor, "/confirm")
        event_names = [event for event, _ in second_round]
        self.assertIn("tool", event_names)
        self.assertIn("done", event_names)
        self.assertEqual(plan.content["sections"][0]["content"], "1. 理解重点概念；2. 完成基础应用。")
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_insert_element_normalizes_position_from_llm_and_executes(self) -> None:
        editor, plan, _ = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "insert_element",
                                "target": "新授",
                                "action": "insert",
                                "parameters": {
                                    "target_section": "新授",
                                    "position": "结尾",
                                    "element_type": "第四部分",
                                    "content": "课堂提问：为什么这里要先做归一化？",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        first_round = await self.collect_events(editor, "在新授中添加第四部分：课堂提问，为什么这里要先做归一化？")
        confirmation_events = [payload for event, payload in first_round if event == "confirmation_required"]
        self.assertEqual(confirmation_events[0]["tool_args"]["position"], "end")
        self.assertEqual(confirmation_events[0]["tool_args"]["content"], "课堂提问：为什么这里要先做归一化？")

        second_round = await self.collect_events(editor, "/confirm")
        self.assertIn("第四部分", plan.content["sections"][2]["content"])
        self.assertIn("课堂提问：为什么这里要先做归一化？", plan.content["sections"][2]["content"])
        self.assertEqual(second_round[-1][0], "done")

    async def test_invalid_modify_task_emits_follow_up_instead_of_guessing(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "insert_element",
                                "target": "新授",
                                "action": "insert",
                                "parameters": {
                                    "target_section": "新授",
                                    "position": "end",
                                    "element_type": "第四部分",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "我需要在新授中添加第四部分：给出完整的示例代码")
        follow_up_events = [payload for event, payload in events if event == "follow_up"]
        self.assertEqual(len(follow_up_events), 1)
        self.assertIn("content", follow_up_events[0]["question"])
        self.assertIsNone(conversation.metadata_json.get("pending_tasks"))
        self.assertIn("pending_follow_up", conversation.metadata_json)

    async def test_compound_modify_tasks_execute_after_single_confirmation(self) -> None:
        editor, plan, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "rewrite_section",
                                "target": "导入",
                                "action": "rewrite",
                                "proposed_content": "改成视频导入。",
                                "parameters": {
                                    "section_type": "导入",
                                    "new_content": "改成视频导入。",
                                    "preserve_duration": True,
                                },
                            },
                            {
                                "type": "modify",
                                "tool_name": "adjust_duration",
                                "target": "新授",
                                "action": "adjust_duration",
                                "parameters": {"section_type": "新授", "new_duration": 20},
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        first_round = await self.collect_events(editor, "把导入改成视频，然后把新授缩短到20分钟")
        confirmation_events = [payload for event, payload in first_round if event == "confirmation_required"]
        self.assertEqual(len(confirmation_events), 1)
        self.assertIn("准备连续执行 2 项修改", confirmation_events[0]["operation_description"])
        self.assertIn("改成视频导入。", confirmation_events[0]["proposed_changes"])
        self.assertIn("new_duration", confirmation_events[0]["proposed_changes"])

        second_round = await self.collect_events(editor, "/confirm")
        self.assertEqual(plan.content["sections"][1]["content"], "改成视频导入。")
        self.assertEqual(plan.content["sections"][2]["duration"], 20)
        self.assertEqual(second_round[-1][0], "done")
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_follow_up_answer_executes_remaining_modify_tasks_without_extra_confirmation(self) -> None:
        editor, plan, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "follow_up",
                                "parameters": {
                                    "question": "你希望改成文本摘要 Agent 还是问答 Agent？",
                                    "options": ["文本摘要 Agent", "问答 Agent"],
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "rewrite_section",
                                "target": "新授",
                                "action": "rewrite",
                                "proposed_content": "给出文本摘要 Agent 的完整代码实现。",
                                "parameters": {
                                    "section_type": "新授",
                                    "new_content": "from agents import SummaryAgent\n\nclass TextSummaryAgent:\n    async def run(self, article: str) -> str:\n        cleaned = article.strip()\n        if not cleaned:\n            return \"\"\n        return SummaryAgent().summarize(cleaned)\n",
                                    "preserve_duration": True,
                                },
                            },
                            {
                                "type": "modify",
                                "tool_name": "adjust_duration",
                                "target": "新授",
                                "action": "adjust_duration",
                                "proposed_content": "把新授时长调整为 20 分钟。",
                                "parameters": {"section_type": "新授", "new_duration": 20},
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        first_round = await self.collect_events(editor, "把新授改成另一个功能的完整代码示例")
        self.assertTrue(any(event == "follow_up" for event, _ in first_round))
        self.assertIn("pending_follow_up", conversation.metadata_json)

        second_round = await self.collect_events(editor, "改成文本摘要 Agent")
        event_names = [event for event, _ in second_round]
        self.assertNotIn("confirmation_required", event_names)
        self.assertIn("tool", event_names)
        self.assertEqual(second_round[-1][0], "done")
        self.assertIn("class TextSummaryAgent", plan.content["sections"][2]["content"])
        self.assertEqual(plan.content["sections"][2]["duration"], 20)
        self.assertNotIn("pending_tasks", conversation.metadata_json)
        self.assertNotIn("pending_follow_up", conversation.metadata_json)

    async def test_placeholder_complete_code_is_blocked_by_quality_guard(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "rewrite_section",
                                "target": "新授",
                                "action": "rewrite",
                                "proposed_content": "给出完整可运行代码。",
                                "parameters": {
                                    "section_type": "新授",
                                    "new_content": "下面给出一个简化示例，实际接入时请按实际情况调整。",
                                    "preserve_duration": True,
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "把新授改成完整可运行代码")
        follow_up_events = [payload for event, payload in events if event == "follow_up"]
        self.assertEqual(len(follow_up_events), 1)
        self.assertIn("示意稿", follow_up_events[0]["question"])
        self.assertIsNone(conversation.metadata_json.get("pending_tasks"))
        self.assertIn("pending_follow_up", conversation.metadata_json)

    async def test_query_task_executes_immediately(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "query",
                                "tool_name": "search_in_plan",
                                "target": "导入",
                                "action": "search",
                                "parameters": {"keyword": "导入"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "这个教案有哪些导入内容")
        event_names = [event for event, _ in events]
        self.assertIn("tool", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("done", event_names)
        tool_result_events = [payload for event, payload in events if event == "tool_result"]
        self.assertIn("找到 1 处与“导入”相关的内容", tool_result_events[0]["summary"])
        self.assertTrue(any(event == "delta" and "导入" in payload["content"] for event, payload in events))
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_get_section_details_query_renders_readable_paragraph_context(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "query",
                                "tool_name": "get_section_details",
                                "target": "新授",
                                "action": "inspect",
                                "parameters": {
                                    "section_type": " 新授： ",
                                    "include_neighbors": "是",
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "看一下新授章节的详细上下文")
        tool_events = [payload for event, payload in events if event == "tool"]
        self.assertEqual(tool_events[0]["arguments"]["section_type"], "新授")
        self.assertIs(tool_events[0]["arguments"]["include_neighbors"], True)
        self.assertTrue(any(event == "delta" and "第1段" in payload["content"] for event, payload in events))
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_paragraph_edit_arguments_normalize_dirty_indices(self) -> None:
        editor, _, _ = self.make_editor([])

        normalized = editor._normalize_tool_arguments(
            "replace_paragraphs_in_section",
            {
                "section_type": " 新授： ",
                "section_index": " 2 ",
                "start_paragraph_index": "第0段",
                "end_paragraph_index": "1。",
                "new_text": "替换后的讲解",
            },
        )

        self.assertEqual(normalized["section_type"], "新授")
        self.assertEqual(normalized["section_index"], 2)
        self.assertEqual(normalized["start_paragraph_index"], 0)
        self.assertEqual(normalized["end_paragraph_index"], 1)
        self.assertEqual(normalized["new_text"], "替换后的讲解")

    async def test_follow_up_task_emits_follow_up_event(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "goal_status": "need_follow_up",
                        "tasks": [
                            {
                                "type": "follow_up",
                                "parameters": {
                                    "question": "你希望把导入改成视频、实验还是故事导入？",
                                    "options": ["视频", "实验", "故事"],
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "帮我优化导入")
        follow_up_events = [payload for event, payload in events if event == "follow_up"]
        self.assertEqual(len(follow_up_events), 1)
        self.assertEqual(follow_up_events[0]["question"], "你希望把导入改成视频、实验还是故事导入？")
        self.assertEqual(follow_up_events[0]["options"], ["视频", "实验", "故事"])
        self.assertEqual(conversation.metadata_json.get("pending_follow_up", {}).get("question"), follow_up_events[0]["question"])

    async def test_legacy_task_only_payload_still_infers_goal_status(self) -> None:
        editor, _, _ = self.make_editor([])

        legacy_modify = editor._parse_task_plan_from_llm_output(
            json.dumps(
                {
                    "tasks": [
                        {
                            "type": "modify",
                            "tool_name": "rewrite_section",
                            "parameters": {
                                "section_type": "导入",
                                "new_content": "新的导入",
                                "preserve_duration": True,
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        legacy_follow_up = editor._parse_task_plan_from_llm_output(
            json.dumps(
                {
                    "tasks": [
                        {
                            "type": "follow_up",
                            "parameters": {
                                "question": "请补充导入要改成什么形式？",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        legacy_complete = editor._parse_task_plan_from_llm_output(
            json.dumps({"tasks": []}, ensure_ascii=False)
        )

        self.assertIsNotNone(legacy_modify)
        self.assertEqual(legacy_modify.goal_status, "need_more_steps")
        self.assertIsNotNone(legacy_follow_up)
        self.assertEqual(legacy_follow_up.goal_status, "need_follow_up")
        self.assertIsNotNone(legacy_complete)
        self.assertEqual(legacy_complete.goal_status, "complete")

    async def test_follow_up_payload_helpers_preserve_resume_context(self) -> None:
        editor, _, _ = self.make_editor([])

        payload = editor._build_follow_up_payload(
            Task(
                type="follow_up",
                parameters={"question": "请补充目标版本", "options": ["A", "B"]},
            )
        )
        decorated = editor._decorate_follow_up_payload(
            payload,
            previous_user_message="继续上一轮",
            root_user_message="最初需求",
            completed_steps=["先查到了目标章节。"],
            remaining_tasks=[
                Task(
                    type="modify",
                    tool_name="rewrite_section",
                    target="新授",
                    action="rewrite",
                    parameters={"section_type": "新授", "new_content": "新的正文"},
                )
            ],
        )

        self.assertEqual(payload["question"], "请补充目标版本")
        self.assertEqual(payload["options"], ["A", "B"])
        self.assertEqual(decorated["previous_user_message"], "继续上一轮")
        self.assertEqual(decorated["root_user_message"], "最初需求")
        self.assertEqual(decorated["completed_steps"], ["先查到了目标章节。"])
        self.assertEqual(len(decorated["remaining_tasks"]), 1)
        self.assertEqual(decorated["remaining_tasks"][0]["tool_name"], "rewrite_section")

    async def test_get_root_user_message_prefers_original_requirement(self) -> None:
        editor, _, _ = self.make_editor([])

        root_message = editor._get_root_user_message(
            "这次补充是 5 分钟",
            {
                "root_user_message": "请帮我设计一个小组讨论活动",
                "previous_user_message": "我想加一段讨论",
            },
        )
        fallback_root = editor._get_root_user_message(
            "补充答案",
            {
                "previous_user_message": "先把导入改成实验演示",
            },
        )

        self.assertEqual(root_message, "请帮我设计一个小组讨论活动")
        self.assertEqual(fallback_root, "先把导入改成实验演示")

    async def test_extract_json_payload_accepts_fenced_json(self) -> None:
        editor, _, _ = self.make_editor([])

        payload = editor._extract_json_payload(
            """```json
            {"goal_status": "complete", "tasks": []}
            ```"""
        )

        self.assertEqual(payload, '{"goal_status": "complete", "tasks": []}')

    async def test_query_only_modify_request_replans_into_confirmation(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "goal_status": "need_more_steps",
                        "tasks": [
                            {
                                "type": "query",
                                "tool_name": "search_in_plan",
                                "target": "教学目标",
                                "action": "search",
                                "parameters": {"keyword": "教学目标"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "goal_status": "need_more_steps",
                        "tasks": [
                            {
                                "type": "modify",
                                "tool_name": "rewrite_section",
                                "target": "教学目标",
                                "action": "rewrite",
                                "proposed_content": "1. 说出本课重点。2. 完成基础练习。",
                                "parameters": {
                                    "section_type": "教学目标",
                                    "new_content": "1. 说出本课重点。2. 完成基础练习。",
                                    "preserve_duration": True,
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        events = await self.collect_events(editor, "教学目标太长了，请拆成两点")
        event_names = [event for event, _ in events]

        self.assertIn("tool", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("confirmation_required", event_names)
        self.assertNotIn("done", event_names)
        self.assertEqual(len(conversation.metadata_json.get("pending_tasks", [])), 1)

    async def test_query_round_can_finish_after_post_execution_review(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "goal_status": "need_more_steps",
                        "tasks": [
                            {
                                "type": "query",
                                "tool_name": "search_in_plan",
                                "target": "导入",
                                "action": "search",
                                "parameters": {"keyword": "导入"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"goal_status": "complete", "tasks": []}, ensure_ascii=False),
            ]
        )

        events = await self.collect_events(editor, "导入部分现在写了什么？")
        event_names = [event for event, _ in events]
        done_events = [payload for event, payload in events if event == "done"]

        self.assertIn("tool", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("done", event_names)
        self.assertNotIn("confirmation_required", event_names)
        self.assertEqual(len(done_events), 1)
        self.assertIn("找到 1 处与“导入”相关的内容。", done_events[0]["reply"])
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_intent_and_replan_prompts_include_completion_criteria_and_structured_tool_results(self) -> None:
        editor, plan, conversation = self.make_editor(
            [
                json.dumps({"goal_status": "complete", "tasks": []}, ensure_ascii=False),
                json.dumps({"goal_status": "complete", "tasks": []}, ensure_ascii=False),
            ]
        )
        recent_ops = [
            SimpleNamespace(
                tool_name="search_in_plan",
                arguments={"keyword": "导入"},
                result={
                    "ok": True,
                    "message": "找到 1 处与“导入”相关的内容。",
                    "matches": [{"section": "导入", "snippet": "生活化导入示例"}],
                },
            )
        ]

        await editor._recognize_intent(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message="把导入改短一点",
        )
        intent_prompt = editor.llm_client.chat.completions.calls[-1]["messages"][1]["content"]

        await editor._plan_next_round_after_execution(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message="把导入改短一点",
            completed_steps=["找到 1 处与“导入”相关的内容。"],
        )
        replan_prompt = editor.llm_client.chat.completions.calls[-1]["messages"][1]["content"]

        self.assertIn("任务完成判据", intent_prompt)
        self.assertIn("goal_status", intent_prompt)
        self.assertIn("最近工具结果（结构化）", intent_prompt)
        self.assertIn("\"tool_name\": \"search_in_plan\"", intent_prompt)
        self.assertIn("\"matches_count\": 1", intent_prompt)
        self.assertIn("只有当必要修改已经被规划并准备执行，或已实际执行完成时，才算完成", intent_prompt)

        self.assertIn("任务完成判据", replan_prompt)
        self.assertIn("goal_status", replan_prompt)
        self.assertIn("最近工具结果（结构化）", replan_prompt)
        self.assertIn("不要把刚刚已经完成的同一查询原样再做一遍", replan_prompt)

    async def test_reply_task_generates_done_without_tool_execution(self) -> None:
        editor, _, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "reply",
                                "response": "当前教案共有 3 个核心章节，分别是教学目标、导入和新授。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "这个教案现在有哪些主要章节")
        event_names = [event for event, _ in events]
        self.assertNotIn("tool", event_names)
        self.assertIn("done", event_names)
        self.assertTrue(any(event == "delta" and "3 个核心章节" in payload["content"] for event, payload in events))
        self.assertNotIn("pending_tasks", conversation.metadata_json)

    async def test_silent_tool_result_still_returns_visible_reply(self) -> None:
        editor, _, _ = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "query",
                                "tool_name": "silent_query",
                                "target": "导入",
                                "action": "search",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        events = await self.collect_events(editor, "看看导入有没有结果")
        self.assertTrue(any(event == "tool" for event, _ in events))
        self.assertTrue(any(event == "tool_result" for event, _ in events))
        self.assertTrue(
            any(
                event == "delta" and "工具没有返回可展示的文本结果" in payload["content"]
                for event, payload in events
            )
        )
        self.assertEqual(events[-1][0], "done")

    async def test_editor_keeps_recent_turn_memory_and_layered_prompt(self) -> None:
        editor, plan, conversation = self.make_editor(
            [
                json.dumps(
                    {
                        "tasks": [
                            {
                                "type": "reply",
                                "response": "我会先围绕导入部分继续优化。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        await self.collect_events(editor, "请继续优化导入部分")
        recent_turns = conversation.metadata_json.get("recent_turns", [])
        self.assertGreaterEqual(len(recent_turns), 2)
        self.assertEqual(recent_turns[0]["role"], "user")
        self.assertEqual(recent_turns[-1]["role"], "assistant")

        editor._get_active_preferences_text = lambda _user_id: ""
        prompt = await editor._build_system_prompt(plan, conversation, [], "继续优化导入")
        self.assertIn("[文档概要]", prompt)
        self.assertIn("[焦点章节]", prompt)
        self.assertIn("[会话记忆]", prompt)

    async def test_system_prompt_renders_structured_temp_preferences(self) -> None:
        editor, plan, conversation = self.make_editor(
            [],
            temp_preferences={
                "teaching_pace": "thorough",
                "interaction_level": "interactive",
                "other_notes": "多用生活化例子。",
            },
        )

        editor._get_active_preferences_text = lambda _user_id: ""
        prompt = await editor._build_system_prompt(plan, conversation, [], "继续优化导入")

        self.assertIn("本次会话请额外遵循以下偏好", prompt)
        self.assertIn("关键内容放慢讲透", prompt)
        self.assertIn("尽量提高互动频率", prompt)
        self.assertIn("多用生活化例子。", prompt)

    async def test_focus_sections_follow_current_request(self) -> None:
        editor, plan, conversation = self.make_editor([])
        conversation.metadata_json = {
            "recent_turns": [
                {"role": "user", "content": "上次先看了导入环节", "kind": "message"},
                {"role": "assistant", "content": "可以继续优化导入", "kind": "reply"},
            ]
        }

        indices = editor._select_focus_section_indices(plan, conversation, [], "继续优化导入部分")

        self.assertIn(1, indices)
        focus_text = editor._build_focus_sections_text(plan, indices)
        self.assertIn("导入", focus_text)
        self.assertNotIn("当前教案没有可用章节", focus_text)

    async def test_context_snapshot_keeps_only_current_plan_and_session_context(self) -> None:
        editor, plan, conversation = self.make_editor([])
        conversation.summary = "上一轮已经把导入改得更生活化。"
        conversation.metadata_json = {
            "recent_turns": [
                {"role": "user", "content": "先保留导入，继续看新授", "kind": "message"},
                {"role": "assistant", "content": "好，我继续聚焦新授。", "kind": "reply"},
            ]
        }

        snapshot = await editor._compose_context_snapshot(
            plan=plan,
            conversation=conversation,
            recent_ops=[],
            user_message="继续优化新授部分",
        )

        self.assertIn("[文档概要]", snapshot)
        self.assertIn("[焦点章节]", snapshot)
        self.assertIn("[会话记忆]", snapshot)
        self.assertIn("[最近操作]", snapshot)
        self.assertNotIn("[参考检索]", snapshot)


if __name__ == "__main__":
    unittest.main()
