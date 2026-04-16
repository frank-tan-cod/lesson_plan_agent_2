from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.presentation_models import PresentationDocument, Slide
from backend.app.schemas import PlanCreate, PresentationCreate, SlidePayload
from backend.app.database import init_db, session_maker
from backend.app.services.plan_service import PlanService
from backend.app.services.presentation_service import PresentationService
from backend.app.services.conversation_service import ConversationService
from backend.app.services.operation_service import OperationService
from backend.app.tools.presentation_tools import (
    ReplacePresentationArgs,
    _match_slide_indices,
    _search_presentation_content,
    change_layout_tool,
    get_slide_details_tool,
    replace_presentation_tool,
    update_slide_content_tool,
)


class PresentationToolsSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = PresentationDocument(
            title="Agent 工作流",
            classroom_script="先讲分步详解，再演示如何初始化 Agent。",
            slides=[
                Slide(title="课程封面", template="title_body", body="认识 Agent"),
                Slide(title="分步详解（1/2）", template="title_body", body="第一步：导入模块"),
                Slide(title="分步详解（2/2）", template="title_body", body="第二步：初始化 LLM"),
            ],
        )

    def test_search_returns_fuzzy_matches_for_nearby_titles(self) -> None:
        matches, match_mode = _search_presentation_content(self.document, "分布详解", max_matches=5)

        self.assertEqual(match_mode, "fuzzy")
        self.assertGreaterEqual(len(matches), 2)
        self.assertEqual(matches[0]["match_type"], "fuzzy")
        self.assertIn("分步详解", matches[0]["title"])
        self.assertGreater(matches[0]["score"], 0.7)

    def test_search_prefers_exact_matches_before_fuzzy_fallback(self) -> None:
        matches, match_mode = _search_presentation_content(self.document, "初始化 LLM", max_matches=5)

        self.assertEqual(match_mode, "exact")
        self.assertEqual(matches[0]["match_type"], "exact")
        self.assertEqual(matches[0]["slide_index"], 2)

    def test_match_slide_indices_supports_fuzzy_title_lookup(self) -> None:
        matches = _match_slide_indices(self.document, "分布详解")

        self.assertEqual(matches[:2], [1, 2])


class PresentationReplaceValidationTests(unittest.TestCase):
    def test_replace_presentation_args_require_non_empty_slides(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ReplacePresentationArgs.model_validate(
                {
                    "title": "Agent 工作流",
                    "classroom_script": "先讲分步详解，再演示如何初始化 Agent。",
                    "slides": [],
                }
            )

        self.assertIn("至少提供 1 页幻灯片", str(ctx.exception))

    def test_replace_presentation_tool_rejects_empty_slides(self) -> None:
        result = replace_presentation_tool(
            plan_id="plan-1",
            title="Agent 工作流",
            classroom_script="先讲分步详解，再演示如何初始化 Agent。",
            slides=[],
        )

        self.assertFalse(result["ok"])
        self.assertIn("至少 1 页幻灯片", result["message"])
        self.assertIn("清空整份演示文稿", result["message"])

    def test_slide_models_tolerate_dirty_optional_field_types(self) -> None:
        slide = Slide.model_validate(
            {
                "template": None,
                "title": 123,
                "subtitle": {"text": "副标题"},
                "body": None,
                "bullet_points": "第一点\n第二点",
                "game_index": "",
                "link_text": {"label": "入口"},
                "notes": ["讲解", "提示"],
            }
        )
        payload = SlidePayload.model_validate(
            {
                "template": None,
                "title": 123,
                "subtitle": {"text": "副标题"},
                "body": None,
                "bullet_points": "第一点\n第二点",
                "game_index": "",
                "link_text": {"label": "入口"},
                "notes": ["讲解", "提示"],
            }
        )

        for item in (slide, payload):
            self.assertEqual(item.template, "title_body")
            self.assertEqual(item.title, "123")
            self.assertEqual(item.subtitle, "{'text': '副标题'}")
            self.assertEqual(item.body, "第一点\n第二点")
            self.assertEqual(item.bullet_points, ["第一点", "第二点"])
            self.assertIsNone(item.game_index)
            self.assertEqual(item.link_text, "{'label': '入口'}")
            self.assertEqual(item.notes, "['讲解', '提示']")


class PresentationLayoutMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def setUp(self) -> None:
        with session_maker() as session:
            lesson_service = PlanService(session)
            lesson = lesson_service.create(
                PlanCreate(
                    title="防溺水知识游戏课教案",
                    content={
                        "sections": [{"type": "新授", "content": "讲解防溺水知识。"}],
                        "games": [
                            {
                                "id": "game-water-safe",
                                "template": "true_false",
                                "title": "防溺水安全对与错",
                                "description": "快速判断以下陈述是否正确。准备好了吗？开始抢答！",
                                "source_section": "课堂小游戏",
                                "learning_goal": "巩固防溺水安全基本知识",
                                "html_url": "/uploads/games/water_safe_tf.html",
                                "data": {
                                    "statements": [
                                        {
                                            "statement": "发现同伴落水，应立刻跳下水去救他",
                                            "answer": False,
                                            "explanation": "应先呼救并寻求成人帮助。",
                                        }
                                    ]
                                },
                            }
                        ],
                    },
                )
            )
            self.lesson_id = lesson.id
            service = PresentationService(session)
            self.presentation = service.create(
                PresentationCreate(
                    title="图片版式测试",
                    content={
                        "title": "图片版式测试",
                        "slides": [
                            {
                                "template": "title_body_image",
                                "title": "实验现象",
                                "body": "观察浮力实验现象",
                                "image_description": "浮力实验照片",
                                "image_url": "/tmp/fake-image.png",
                            }
                        ],
                    },
                    metadata={"source_plan_id": self.lesson_id, "generated_from": "lesson_plan"},
                )
            )
            self.plan_id = self.presentation.id

    def tearDown(self) -> None:
        with session_maker() as session:
            service = PresentationService(session)
            service.delete(self.plan_id)
            PlanService(session).delete(self.lesson_id)

    def _get_slide(self) -> dict[str, object]:
        with session_maker() as session:
            service = PresentationService(session)
            plan = service.get(self.plan_id)
            assert plan is not None
            return plan.content["slides"][0]

    def test_change_layout_to_text_template_clears_image_fields(self) -> None:
        result = change_layout_tool(
            plan_id=self.plan_id,
            slide_index=0,
            new_layout="title_content",
        )

        slide = self._get_slide()
        self.assertTrue(result["ok"])
        self.assertEqual(slide["template"], "title_body")
        self.assertIsNone(slide["image_description"])
        self.assertIsNone(slide["image_url"])
        self.assertTrue(result["cleared_image_fields"])
        self.assertIn("移除了图片占位", result["message"])

    def test_update_slide_content_template_change_clears_stale_image_fields(self) -> None:
        result = update_slide_content_tool(
            plan_id=self.plan_id,
            slide_index=0,
            template="title_body",
            body="改成纯文字说明",
        )

        slide = self._get_slide()
        self.assertTrue(result["ok"])
        self.assertEqual(slide["template"], "title_body")
        self.assertEqual(slide["body"], "改成纯文字说明")
        self.assertIsNone(slide["image_description"])
        self.assertIsNone(slide["image_url"])
        self.assertTrue(result["cleared_image_fields"])

    def test_update_slide_content_rejects_process_placeholder_text(self) -> None:
        original_slide = self._get_slide()

        result = update_slide_content_tool(
            plan_id=self.plan_id,
            slide_index=0,
            body="准备将内容合并到第5页。需要先搜索确定具体内容后填充。",
        )

        slide = self._get_slide()
        self.assertFalse(result["ok"])
        self.assertIn("不会写入 PPT", result["message"])
        self.assertEqual(slide["body"], original_slide["body"])

    def test_replace_presentation_logs_compact_operation_payload(self) -> None:
        with session_maker() as session:
            conversation = ConversationService(session).create(self.plan_id)

        result = replace_presentation_tool(
            plan_id=self.plan_id,
            conversation_id=conversation.id,
            title="新的图片版式测试",
            classroom_script="这一版按三步讲清楚实验现象、原因和结论。",
            slides=[
                {
                    "title": "封面",
                    "template": "title_body",
                    "body": "课程导入与目标说明",
                    "notes": "这里先讲学习目标，再提示观察任务。",
                },
                {
                    "title": "实验步骤",
                    "template": "title_body",
                    "body": "第一步准备器材\n第二步观察现象\n第三步记录结果",
                    "image_description": "实验器材照片",
                },
            ],
        )

        self.assertTrue(result["ok"])

        with session_maker() as session:
            operations = OperationService(session).list_by_conversation(conversation.id)

        replace_op = next(item for item in operations if item.tool_name == "replace_presentation")
        self.assertEqual(replace_op.arguments["slides_count"], 2)
        self.assertEqual(len(replace_op.arguments["slides_preview"]), 2)
        self.assertNotIn("slides", replace_op.arguments)
        self.assertIn("classroom_script_preview", replace_op.arguments)
        self.assertEqual(replace_op.result["slides_count"], 2)
        self.assertNotIn("slides", replace_op.result)

    def test_replace_presentation_resolves_game_placeholder_link_fields(self) -> None:
        result = replace_presentation_tool(
            plan_id=self.plan_id,
            title="新的图片版式测试",
            classroom_script="把小游戏链接移动到对应正文页。",
            slides=[
                {
                    "title": "综合小挑战",
                    "template": "title_body_image",
                    "body": (
                        "情景：海边玩耍，弟弟不见，小桶漂向深水区……\n"
                        "这时你应该怎么做？\n"
                        "[游戏入口：点击此处挑战防溺水快速对错抢答]"
                    ),
                    "image_description": "海边安全情景插图",
                    "source_section": "游戏与实践",
                }
            ],
        )

        slide = self._get_slide()
        self.assertTrue(result["ok"])
        self.assertEqual(slide["link_text"], "游戏入口：点击此处挑战防溺水快速对错抢答")
        self.assertEqual(slide["link_url"], "http://127.0.0.1:8000/uploads/games/water_safe_tf.html")
        self.assertIn("游戏入口：点击此处挑战防溺水快速对错抢答", slide["body"])

    def test_replace_presentation_resolves_structured_game_index(self) -> None:
        result = replace_presentation_tool(
            plan_id=self.plan_id,
            title="新的图片版式测试",
            classroom_script="把小游戏链接移动到对应正文页。",
            slides=[
                {
                    "title": "综合小挑战",
                    "template": "title_body",
                    "body": "先完成互动，再说说你的判断依据。\n[[GAME_LINK:1]]",
                    "game_index": 1,
                    "source_section": "游戏与实践",
                }
            ],
        )

        slide = self._get_slide()
        self.assertTrue(result["ok"])
        self.assertEqual(slide["game_index"], 1)
        self.assertEqual(slide["link_text"], "互动入口：点击打开小游戏")
        self.assertEqual(slide["link_url"], "http://127.0.0.1:8000/uploads/games/water_safe_tf.html")
        self.assertNotIn("[[GAME_LINK:1]]", slide["body"])

    def test_get_slide_details_logs_compact_slide_summary(self) -> None:
        with session_maker() as session:
            conversation = ConversationService(session).create(self.plan_id)

        result = update_slide_content_tool(
            plan_id=self.plan_id,
            slide_index=0,
            title="实验现象",
            body="先观察泡沫板上浮，再比较不同物体的沉浮状态。",
            notes="强调观察顺序和记录方法。",
            conversation_id=conversation.id,
        )
        self.assertTrue(result["ok"])

        detail_result = get_slide_details_tool(
            plan_id=self.plan_id,
            conversation_id=conversation.id,
            slide_index=0,
        )
        self.assertTrue(detail_result["ok"])

        with session_maker() as session:
            operations = OperationService(session).list_by_conversation(conversation.id)

        detail_op = next(item for item in operations if item.tool_name == "get_slide_details")
        self.assertEqual(detail_op.arguments["slide_index"], 0)
        self.assertIn("slide", detail_op.result)
        self.assertIn("body_preview", detail_op.result["slide"])
        self.assertNotIn("body", detail_op.result["slide"])
        self.assertIn("notes_preview", detail_op.result["slide"])


if __name__ == "__main__":
    unittest.main()
