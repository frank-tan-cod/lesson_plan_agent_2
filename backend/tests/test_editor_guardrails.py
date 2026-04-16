from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.schemas import Task
from backend.app.services.editor_guardrails import EditorGuardrails


class SlideArgs(BaseModel):
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class ReplacePresentationArgs(BaseModel):
    slides: list[SlideArgs] = Field(..., min_length=1)


class RewriteSectionArgs(BaseModel):
    section_type: str = Field(..., min_length=1)
    new_content: str = Field(..., min_length=1)


class FakeTool:
    def __init__(self, name: str, args_schema: type[BaseModel]) -> None:
        self.name = name
        self.args_schema = args_schema


class FakeToolsRegistry:
    def __init__(self) -> None:
        self.tools = {
            "replace_presentation": FakeTool("replace_presentation", ReplacePresentationArgs),
            "rewrite_section": FakeTool("rewrite_section", RewriteSectionArgs),
        }

    def get_tool(self, name: str) -> FakeTool:
        return self.tools[name]


class EditorGuardrailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guardrails = EditorGuardrails(
            FakeToolsRegistry(),
            json_ready=lambda value: value,
            clean_text=lambda value: str(value or "").strip(),
        )

    def test_validate_tool_arguments_humanizes_slide_field_errors(self) -> None:
        normalized, issues = self.guardrails.validate_tool_arguments(
            "replace_presentation",
            {"slides": [{"title": None, "body": None}]},
        )

        self.assertEqual(normalized, {"slides": [{"title": None, "body": None}]})
        self.assertEqual(
            issues,
            [
                "第 1 页的标题: 需要填写文本；如果不需要内容，请传空字符串",
                "第 1 页的正文: 需要填写文本；如果不需要内容，请传空字符串",
            ],
        )

    def test_build_invalid_task_follow_up_preserves_ppt_wording(self) -> None:
        follow_up = self.guardrails.build_invalid_task_follow_up(
            Task(type="modify", tool_name="replace_presentation", action="layout", parameters={}),
            ["slides.12.title: Input should be a valid string"],
            resolve_tool_name=lambda task: task.tool_name,
        )

        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("整体重排这份 PPT", follow_up["question"])
        self.assertIn("第 13 页的标题", follow_up["question"])
        self.assertNotIn("slides.12.title", follow_up["question"])

    def test_build_content_quality_context_tracks_complete_runnable_requests(self) -> None:
        context = self.guardrails.build_content_quality_context(
            "把这一段改成完整代码",
            pending_follow_up={"question": "请补充要实现哪个 agent", "previous_user_message": "需要可运行版本"},
        )

        self.assertTrue(context["requires_complete_content"])
        self.assertTrue(context["requires_runnable_code"])
        self.assertIn("可运行版本", context["request_text"])

    def test_validate_task_content_quality_blocks_placeholder_code(self) -> None:
        issues = self.guardrails.validate_task_content_quality(
            Task(
                type="modify",
                tool_name="rewrite_section",
                action="rewrite",
                target="新授",
                parameters={
                    "section_type": "新授",
                    "new_content": "下面给出一个简化示例，实际接入时请按实际情况调整。",
                },
            ),
            {"requires_complete_content": True, "requires_runnable_code": True},
            build_task_arguments=lambda task: dict(task.parameters),
        )

        self.assertEqual(
            issues,
            [
                "包含“简化示例/简化实现”一类表述",
                "要求后续再按实际情况调整",
                "内容长度或结构仍不像可直接运行的代码正文",
            ],
        )

    def test_build_content_quality_follow_up_mentions_subject_and_reason(self) -> None:
        follow_up = self.guardrails.build_content_quality_follow_up(
            Task(type="modify", tool_name="rewrite_section", action="rewrite", target="新授", parameters={}),
            ["内容仍像待补全草稿"],
            build_task_arguments=lambda task: {"new_content": "TODO"},
            infer_task_subject=lambda arguments, fallback: fallback or "新授",
        )

        self.assertEqual(follow_up["type"], "follow_up")
        self.assertIn("我还不能可靠rewrite“新授”", follow_up["question"])
        self.assertIn("内容仍像待补全草稿", follow_up["question"])


if __name__ == "__main__":
    unittest.main()
