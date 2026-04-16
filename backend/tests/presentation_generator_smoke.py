"""Smoke test for generating a presentation from a lesson plan."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _FakeCompletions:
    def __init__(self) -> None:
        self.call_count = 0

    def create(self, **_: object) -> SimpleNamespace:
        self.call_count += 1
        payload = (
            {
                "title": "浮力课件",
                "slides": [
                    {
                        "template": "title_body",
                        "title": "浮力",
                        "body": "请同学们观察生活中的浮力现象，并说说你的第一印象。",
                        "image_description": "一张水中物体受力示意图",
                        "notes": "开场引导学生回顾生活经验。",
                        "source_section": "导入",
                    },
                    {
                        "template": "title_body",
                        "title": "学习目标",
                        "body": "理解浮力概念\n能分析受力情况",
                        "image_description": "",
                        "notes": "强调本课核心任务。",
                        "source_section": "教学目标",
                    },
                ],
                "classroom_script": "浮力\n生活中的浮力现象\n\n学习目标\n理解浮力概念\n能分析受力情况",
            }
            if self.call_count == 1
            else {
                "title": "浮力课件",
                "slides": [
                    {
                        "template": "title_body_image",
                        "title": "浮力",
                        "body": "生活中的浮力现象\n先看图，再说发现",
                        "image_description": "一张水中物体受力示意图",
                        "notes": "开场引导学生回顾生活经验。",
                        "source_section": "导入",
                    },
                    {
                        "template": "title_body",
                        "title": "学习目标",
                        "body": "理解浮力概念\n能分析受力情况",
                        "image_description": "",
                        "notes": "强调本课核心任务。",
                        "source_section": "教学目标",
                    },
                ],
                "classroom_script": "浮力\n生活中的浮力现象\n\n学习目标\n理解浮力概念\n能分析受力情况",
            }
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ]
        )


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


async def main() -> None:
    """Run a minimal generate-presentation smoke test."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "presentation_generator.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

        from backend.app.database import init_db, session_maker
        from backend.app.routers.plans import generate_presentation
        from backend.app.schemas import GeneratePresentationRequest, PlanCreate, PresentationStylePayload
        from backend.app.services.plan_service import PlanService
        from backend.app.services.presentation_service import PresentationService

        init_db()

        with session_maker() as session:
            plan = PlanService(session, user_id="user-1").create(
                PlanCreate(
                    title="浮力教案",
                    subject="物理",
                    grade="八年级",
                    content={
                        "sections": [
                            {"type": "导入", "content": "观察木块在水中的现象。", "duration": 5},
                            {"type": "新授", "content": "讲解浮力定义和方向。", "duration": 20},
                            {"type": "练习", "content": "分析不同物体的沉浮情况。", "duration": 10},
                        ],
                        "games": [
                            {
                                "id": "game_demo",
                                "template": "single_choice",
                                "title": "浮力快答",
                                "description": "根据课堂内容完成选择题。",
                                "source_section": "练习",
                                "learning_goal": "判断浮力变化",
                                "html_url": "/uploads/games/game_demo.html",
                                "data": {
                                    "questions": [
                                        {
                                            "stem": "浮力方向通常怎样？",
                                            "options": ["竖直向上", "竖直向下"],
                                            "answer": "竖直向上",
                                            "explanation": "浮力通常竖直向上。",
                                        }
                                    ]
                                },
                            }
                        ],
                    },
                )
            )

        client = _FakeOpenAIClient()
        with patch(
            "backend.app.services.presentation_generator._get_llm_client",
            return_value=client,
        ):
            with session_maker() as session:
                response = await generate_presentation(
                    plan_id=plan.id,
                    data=GeneratePresentationRequest(
                        additional_files=[],
                        course_context="补充一个浮力实验案例",
                        presentation_style=PresentationStylePayload(
                            theme="forest_green",
                            density="balanced",
                            school_name="示范学校",
                        ),
                    ),
                    db=session,
                    user_id="user-1",
                )

        with session_maker() as session:
            created = PresentationService(session, user_id="user-1").get(response.presentation_id)
            refreshed_plan = PlanService(session, user_id="user-1").get(plan.id)

        assert created is not None
        assert refreshed_plan is not None
        slides = created.content.get("slides", [])
        assert len(slides) > 0
        assert slides[0]["template"] == "title_body_image"
        assert "body" in slides[0]
        assert slides[0]["image_description"] == "一张水中物体受力示意图"
        assert slides[-1]["title"] == "浮力快答"
        assert "互动入口：http://127.0.0.1:8000/uploads/games/game_demo.html" in slides[-1]["body"]
        assert slides[-1]["source_section"] == "课堂小游戏"
        assert client.chat.completions.call_count == 2
        assert created.content.get("classroom_script")
        assert created.metadata_json.get("presentation_style", {}).get("theme") == "forest_green"
        assert refreshed_plan.metadata_json.get("latest_generated_presentation_id") == response.presentation_id
        assert refreshed_plan.metadata_json.get("latest_generated_presentation_title") == created.title
        assert refreshed_plan.metadata_json.get("generated_presentation_ids") == [response.presentation_id]
        assert refreshed_plan.metadata_json.get("presentation_style", {}).get("school_name") == "示范学校"

        print("Presentation generator smoke test passed.")
        print(f"Presentation id: {response.presentation_id}")
        print(f"Slides: {len(slides)}")


if __name__ == "__main__":
    asyncio.run(main())
