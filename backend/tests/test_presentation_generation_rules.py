from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.services.presentation_generator import (  # noqa: E402
    _append_game_slides_from_plan,
    _build_generation_prompt,
    _clean_projection_fragment,
    _normalize_generated_content,
)
from backend.app.schemas import PresentationContent, SlidePayload  # noqa: E402
from backend.app.presentation_models import paginate_slide_text  # noqa: E402


class PresentationGenerationRulesTests(unittest.TestCase):
    def test_generation_prompt_avoids_preset_slide_flow(self) -> None:
        plan = type(
            "Plan",
            (),
            {
                "title": "欧姆定律",
                "content": {
                    "sections": [
                        {"type": "导入", "content": "从手电筒电路引入。"},
                        {"type": "实验", "content": "测量电流、电压和电阻关系。"},
                    ]
                },
            },
        )()

        prompt = _build_generation_prompt(
            plan=plan,
            extra_context="无额外参考资料。",
            course_context="补充一个生活案例。",
        )

        self.assertIn("是否包含封面/目录/过渡页/总结页", prompt)
        self.assertIn("不要机械套用固定流程或固定页型", prompt)
        self.assertNotIn("包含封面和小结", prompt)

    def test_generation_prompt_excludes_structured_games_and_teaches_game_index_binding(self) -> None:
        plan = type(
            "Plan",
            (),
            {
                "title": "浮力",
                "content": {
                    "sections": [{"type": "练习", "content": "回顾浮力大小规律。"}],
                    "games": [
                        {
                            "title": "浮力快答",
                            "template": "single_choice",
                            "data": {"questions": [{"stem": "示例题", "options": ["A", "B"], "answer": "A"}]},
                        }
                    ],
                },
            },
        )()

        prompt = _build_generation_prompt(
            plan=plan,
            extra_context="无额外参考资料。",
            course_context="无补充。",
        )

        self.assertIn("写 `game_index`", prompt)
        self.assertIn("未引用的小游戏兜底追加到 PPT 末尾", prompt)
        self.assertIn("最多只保留一句过渡提示", prompt)
        self.assertIn("已接管的小游戏", prompt)
        self.assertIn("浮力快答：课堂互动巩固", prompt)
        self.assertIn("不要手写小游戏链接", prompt)
        self.assertIn("不要额外生成单独的“课堂小游戏”", prompt)
        self.assertIn("错误示例", prompt)
        self.assertIn("正确示例", prompt)
        self.assertNotIn('"games"', prompt)

    def test_single_slide_payload_is_preserved_without_forced_summary(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "单页课件",
                "classroom_script": "只保留一页总览。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "课程总览",
                        "body": "本课只需要一页展示整体任务与交付物。",
                    }
                ],
            },
            fallback_title="单页课件",
        )

        self.assertEqual(len(content.slides), 1)
        self.assertEqual(content.slides[0].title, "课程总览")

    def test_title_subtitle_slide_keeps_subtitle_without_body(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "课程封面",
                "classroom_script": "封面后直接进入课堂。",
                "slides": [
                    {
                        "template": "title_subtitle",
                        "title": "认识 AI Agent",
                        "subtitle": "从可观察现象入手，再进入课堂任务",
                    }
                ],
            },
            fallback_title="课程封面",
        )

        self.assertEqual(len(content.slides), 1)
        self.assertEqual(content.slides[0].template, "title_subtitle")
        self.assertEqual(content.slides[0].subtitle, "从可观察现象入手，再进入课堂任务")
        self.assertEqual(content.slides[0].body, "")

    def test_normalize_generated_content_treats_blank_game_index_as_none(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "浮力",
                "classroom_script": "先讲浮力概念，再进入课堂互动。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "浮力概念",
                        "body": "浮力方向通常竖直向上。",
                        "game_index": "",
                    }
                ],
            },
            fallback_title="浮力",
        )

        self.assertEqual(len(content.slides), 1)
        self.assertIsNone(content.slides[0].game_index)

    def test_first_slide_is_promoted_to_cover_when_title_matches_deck_title(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "欧姆定律",
                "classroom_script": "封面后进入实验探究。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "欧姆定律",
                        "body": "从生活电路现象走进本课。",
                        "source_section": "导入",
                    },
                    {
                        "template": "title_body",
                        "title": "实验探究",
                        "body": "测一测电压、电流和电阻的关系。",
                    },
                ],
            },
            fallback_title="欧姆定律",
        )

        self.assertEqual(content.slides[0].template, "title_subtitle")
        self.assertEqual(content.slides[0].subtitle, "从生活电路现象走进本课")
        self.assertEqual(content.slides[0].body, "")

    def test_transition_like_slide_is_promoted_to_title_subtitle(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "浮力探究",
                "classroom_script": "中间插入活动过渡页。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "导入",
                        "body": "先回顾生活中的沉浮现象。",
                    },
                    {
                        "template": "title_body",
                        "title": "任务一：观察实验",
                        "body": "接下来我们一起先看现象，再说判断依据。",
                        "source_section": "活动一",
                    },
                    {
                        "template": "title_body",
                        "title": "得出结论",
                        "body": "比较木块和铁块受到的浮力。",
                    },
                ],
            },
            fallback_title="浮力探究",
        )

        self.assertEqual(content.slides[1].template, "title_subtitle")
        self.assertEqual(content.slides[1].subtitle, "先看现象，再说判断依据")
        self.assertEqual(content.slides[1].body, "")

    def test_image_description_promotes_slide_to_image_template(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "实验观察",
                "classroom_script": "先观察图片，再进入结论。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "观察现象",
                        "body": "看图，说出你看到了哪些沉浮变化。",
                        "image_description": "烧杯中的浮沉实验照片",
                    }
                ],
            },
            fallback_title="实验观察",
        )

        self.assertEqual(len(content.slides), 1)
        self.assertEqual(content.slides[0].template, "title_body_image")

    def test_long_image_slide_is_split_into_image_and_follow_up_text_page(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "显微镜观察",
                "classroom_script": "先看图，再补充说明。",
                "slides": [
                    {
                        "template": "title_body_image",
                        "title": "观察记录",
                        "body": (
                            "先观察细胞边缘的轮廓和颜色变化。\n"
                            "再比较不同样本之间的差异。\n"
                            "记录显微镜下看到的细节特征。\n"
                            "尝试归纳共同点和不同点。\n"
                            "最后用一句话概括你的发现。"
                        ),
                        "image_description": "显微镜观察图",
                    }
                ],
            },
            fallback_title="显微镜观察",
        )

        self.assertEqual(len(content.slides), 2)
        self.assertEqual(content.slides[0].template, "title_body_image")
        self.assertEqual(content.slides[1].template, "title_body")
        self.assertIsNone(content.slides[1].image_description)
        self.assertIn("续", content.slides[1].title)

    def test_summary_slide_body_is_compacted_into_projection_lines(self) -> None:
        content = _normalize_generated_content(
            {
                "title": "课堂小结",
                "classroom_script": "最后回顾本课规律。",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "课堂小结",
                        "body": (
                            "通过刚才的实验我们可以发现，排开液体越多，浮力通常越大；"
                            "同学们需要记住，浮力方向总是竖直向上；"
                            "最后把规律带回生活现象中去解释。"
                        ),
                        "source_section": "总结",
                    }
                ],
            },
            fallback_title="课堂小结",
        )

        self.assertEqual(content.slides[0].template, "title_body")
        self.assertIn("排开液体越多，浮力通常越大", content.slides[0].body)
        self.assertIn("浮力方向总是竖直向上", content.slides[0].body)
        self.assertNotIn("通过刚才的实验我们可以发现", content.slides[0].body)
        self.assertGreaterEqual(len(content.slides[0].bullet_points), 2)

    def test_paginate_slide_text_rebalances_sparse_tail_page(self) -> None:
        text = "\n".join(f"要点 {index}" for index in range(1, 13))
        pages = paginate_slide_text(text, chars_per_line=20, max_lines=11)

        self.assertEqual(len(pages), 2)
        self.assertEqual(len([line for line in pages[0].splitlines() if line.strip()]), 6)
        self.assertEqual(len([line for line in pages[1].splitlines() if line.strip()]), 6)

    def test_paginate_slide_text_keeps_light_overflow_on_single_page(self) -> None:
        text = "\n".join(["观察现象", "提出猜想", "动手实验", "记录结果", "说说发现"])
        pages = paginate_slide_text(text, chars_per_line=12, max_lines=4)

        self.assertEqual(len(pages), 1)
        self.assertIn("说说发现", pages[0])

    def test_paginate_slide_text_keeps_split_when_overflow_page_has_real_weight(self) -> None:
        text = "\n".join(["观察现象", "提出猜想", "动手实验", "记录结果", "整理实验并归纳结论"])
        pages = paginate_slide_text(text, chars_per_line=12, max_lines=4)

        self.assertEqual(len(pages), 2)
        self.assertIn("整理实验并归纳结论", pages[1])

    def test_clean_projection_fragment_preserves_short_statement(self) -> None:
        cleaned = _clean_projection_fragment("我们可以发现浮力增大")

        self.assertEqual(cleaned, "我们可以发现浮力增大")

    def test_append_game_slides_replaces_probable_duplicate_game_draft(self) -> None:
        content = PresentationContent(
            title="浮力课件",
            classroom_script="先讲浮力，再做小游戏。",
            slides=[
                SlidePayload(
                    template="title_body",
                    title="浮力概念",
                    body="理解浮力的方向和大小规律。",
                    source_section="新授",
                ),
                SlidePayload(
                    template="title_body",
                    title="浮力快答",
                    body="根据课堂内容完成选择题。",
                    source_section="练习",
                ),
            ],
        )
        plan = type(
            "Plan",
            (),
            {
                "content": {
                    "sections": [{"type": "练习", "content": "回顾浮力大小规律。"}],
                    "games": [
                        {
                            "id": "game_demo",
                            "title": "浮力快答",
                            "template": "single_choice",
                            "description": "根据课堂内容完成选择题。",
                            "source_section": "练习",
                            "learning_goal": "判断浮力变化",
                            "html_url": "/uploads/games/game_demo.html",
                            "data": {"questions": [{"stem": "示例题", "options": ["A", "B"], "answer": "A"}]},
                        }
                    ],
                }
            },
        )()

        updated = _append_game_slides_from_plan(content, plan)

        self.assertEqual(len(updated.slides), 2)
        self.assertEqual(updated.slides[0].title, "浮力概念")
        self.assertEqual(updated.slides[1].title, "浮力快答")
        self.assertIn("互动入口：点击打开小游戏", updated.slides[1].body)
        self.assertEqual(updated.slides[1].link_text, "互动入口：点击打开小游戏")
        self.assertEqual(updated.slides[1].link_url, "http://127.0.0.1:8000/uploads/games/game_demo.html")
        self.assertEqual(updated.slides[1].source_section, "课堂小游戏")

    def test_append_game_slides_binds_explicit_game_index_in_place(self) -> None:
        content = PresentationContent(
            title="浮力课件",
            classroom_script="先讲浮力，再做小游戏。",
            slides=[
                SlidePayload(
                    template="title_body",
                    title="综合挑战",
                    body="完成课堂小游戏后，说说你的判断依据。",
                    source_section="练习",
                    game_index=1,
                )
            ],
        )
        plan = type(
            "Plan",
            (),
            {
                "content": {
                    "games": [
                        {
                            "id": "game_demo",
                            "title": "浮力快答",
                            "template": "single_choice",
                            "description": "根据课堂内容完成选择题。",
                            "source_section": "练习",
                            "learning_goal": "判断浮力变化",
                            "html_url": "/uploads/games/game_demo.html",
                            "data": {"questions": [{"stem": "示例题", "options": ["A", "B"], "answer": "A"}]},
                        }
                    ],
                }
            },
        )()

        updated = _append_game_slides_from_plan(content, plan)

        self.assertEqual(len(updated.slides), 1)
        self.assertEqual(updated.slides[0].game_index, 1)
        self.assertEqual(updated.slides[0].link_text, "互动入口：点击打开小游戏")
        self.assertEqual(updated.slides[0].link_url, "http://127.0.0.1:8000/uploads/games/game_demo.html")
        self.assertIn("互动入口：点击打开小游戏", updated.slides[0].body)

    def test_append_game_slides_removes_similar_game_explanation_slide(self) -> None:
        content = PresentationContent(
            title="防溺水知识游戏课",
            classroom_script="先学习安全知识，再做互动巩固。",
            slides=[
                SlidePayload(
                    template="title_body",
                    title="安全游泳，快乐一夏",
                    body="一起学习防溺水常识。",
                    source_section="导入",
                ),
                SlidePayload(
                    template="title_body",
                    title="游戏一：快速抢答“对与错”",
                    body="快速判断以下陈述是否正确。准备好了吗？开始抢答！",
                    source_section="新授",
                ),
            ],
        )
        plan = type(
            "Plan",
            (),
            {
                "content": {
                    "games": [
                        {
                            "id": "water_safe_tf",
                            "title": "防溺水安全对与错",
                            "template": "true_false",
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
                }
            },
        )()

        updated = _append_game_slides_from_plan(content, plan)

        self.assertEqual(len(updated.slides), 2)
        self.assertEqual(updated.slides[0].title, "安全游泳，快乐一夏")
        self.assertEqual(updated.slides[1].title, "防溺水安全对与错")
        self.assertNotIn("游戏一：快速抢答“对与错”", [slide.title for slide in updated.slides])
        self.assertIn("互动入口：点击打开小游戏", updated.slides[1].body)


if __name__ == "__main__":
    unittest.main()
