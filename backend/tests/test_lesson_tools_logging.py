from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PlanCreate
from backend.app.services.conversation_service import ConversationService
from backend.app.services.operation_service import OperationService
from backend.app.services.plan_service import PlanService
from backend.app.tools.lesson_tools import (
    get_section_details_tool,
    replace_paragraphs_in_section_tool,
    search_in_plan_tool,
)


class LessonToolsLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def setUp(self) -> None:
        with session_maker() as session:
            plan_service = PlanService(session)
            conversation_service = ConversationService(session)
            plan = plan_service.create(
                PlanCreate(
                    title="教案日志瘦身测试",
                    subject="语文",
                    grade="三年级",
                    content={
                        "sections": [
                            {"type": "导入", "content": "通过图片导入新课，并让学生先观察再回答问题。", "duration": 5},
                            {
                                "type": "新授",
                                "content": (
                                    "第一段：讲授重点知识，并结合生活化例子帮助学生理解。\n\n"
                                    "第二段：引导学生观察例子，再说出自己的发现。\n\n"
                                    "第三段：安排小组讨论并记录结论。"
                                ),
                                "duration": 20,
                                "elements": [
                                    {"type": "question", "content": "你看到了什么现象？"},
                                    {"type": "activity", "content": "小组合作整理观察结果。"},
                                ],
                            },
                            {"type": "总结", "content": "回顾本课重点并布置作业。", "duration": 10},
                        ]
                    },
                )
            )
            conversation = conversation_service.create(plan.id)
            self.plan_id = plan.id
            self.conversation_id = conversation.id

    def tearDown(self) -> None:
        with session_maker() as session:
            PlanService(session).delete(self.plan_id)

    def test_get_section_details_logs_compact_section_summary(self) -> None:
        result = get_section_details_tool(
            plan_id=self.plan_id,
            conversation_id=self.conversation_id,
            section_type="新授",
        )
        self.assertTrue(result["ok"])

        with session_maker() as session:
            operations = OperationService(session).list_by_conversation(self.conversation_id)

        detail_op = next(item for item in operations if item.tool_name == "get_section_details")
        self.assertEqual(detail_op.arguments["section_type"], "新授")
        self.assertIn("section", detail_op.result)
        self.assertIn("content_preview", detail_op.result["section"])
        self.assertIn("paragraphs_preview", detail_op.result["section"])
        self.assertIn("elements_preview", detail_op.result["section"])
        self.assertNotIn("content", detail_op.result["section"])
        self.assertNotIn("raw_section", detail_op.result["section"])

    def test_replace_paragraphs_logs_preview_instead_of_full_text(self) -> None:
        result = replace_paragraphs_in_section_tool(
            plan_id=self.plan_id,
            conversation_id=self.conversation_id,
            section_type="新授",
            start_paragraph_index=1,
            new_text="替换后的新段落会更长一些，用于验证操作日志里不会原样存整段正文，而只会保留精简预览。",
        )
        self.assertTrue(result["ok"])

        with session_maker() as session:
            operations = OperationService(session).list_by_conversation(self.conversation_id)

        replace_op = next(item for item in operations if item.tool_name == "replace_paragraphs_in_section")
        self.assertEqual(replace_op.arguments["section_type"], "新授")
        self.assertIn("new_text_preview", replace_op.arguments)
        self.assertNotIn("new_text", replace_op.arguments)

    def test_search_in_plan_logs_match_preview_instead_of_full_matches(self) -> None:
        result = search_in_plan_tool(
            plan_id=self.plan_id,
            conversation_id=self.conversation_id,
            keyword="观察",
        )
        self.assertTrue(result["ok"])

        with session_maker() as session:
            operations = OperationService(session).list_by_conversation(self.conversation_id)

        search_op = next(item for item in operations if item.tool_name == "search_in_plan")
        self.assertEqual(search_op.arguments["keyword"], "观察")
        self.assertIn("matches_count", search_op.result)
        self.assertIn("matches_preview", search_op.result)
        self.assertNotIn("matches", search_op.result)


if __name__ == "__main__":
    unittest.main()
