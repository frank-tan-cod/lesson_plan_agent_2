from __future__ import annotations

import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PlanCreate
from backend.app.services.export_service import ExportService, ExportUnavailableError
from backend.app.services.plan_service import PlanService


class ExportServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        init_db()

    def test_pdf_export_preserves_chinese_text(self) -> None:
        try:
            from pypdf import PdfReader
        except ImportError:
            self.skipTest("pypdf 未安装，跳过 PDF 中文导出校验。")

        with tempfile.TemporaryDirectory() as temp_dir:
            with session_maker() as session:
                plan_service = PlanService(session, user_id=f"test-{uuid.uuid4()}")
                export_service = ExportService(plan_service)

                plan = plan_service.create(
                    PlanCreate(
                        title="浮力实验教案",
                        subject="物理",
                        grade="八年级",
                        content={
                            "sections": [
                                {
                                    "title": "教学目标",
                                    "content": "理解浮力概念，并能结合鸡蛋沉浮实验解释现象。",
                                },
                                {
                                    "title": "教学过程",
                                    "content": "- 导入：观察鸡蛋在盐水中的变化\n- 讨论：比较清水和盐水中的不同现象",
                                },
                            ]
                        },
                    )
                )

                try:
                    pdf_bytes = export_service.export_to_pdf(plan.id)
                except ExportUnavailableError as exc:
                    self.skipTest(str(exc))

                pdf_path = Path(temp_dir) / "lesson.pdf"
                pdf_path.write_bytes(pdf_bytes)
                extracted_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)

                self.assertIn("浮力实验教案", extracted_text)
                self.assertIn("教学目标", extracted_text)
                self.assertIn("鸡蛋沉浮实验", extracted_text)

    def test_docx_export_includes_mini_games_section(self) -> None:
        from docx import Document

        with session_maker() as session:
            plan_service = PlanService(session, user_id=f"test-{uuid.uuid4()}")
            export_service = ExportService(plan_service)

            plan = plan_service.create(
                PlanCreate(
                    title="浮力实验教案",
                    subject="物理",
                    grade="八年级",
                    content={
                        "sections": [
                            {
                                "title": "教学目标",
                                "content": "理解浮力概念，并能结合鸡蛋沉浮实验解释现象。",
                            }
                        ],
                        "games": [
                            {
                                "id": "game_demo",
                                "template": "single_choice",
                                "title": "浮力快答",
                                "description": "选择正确答案",
                                "source_section": "教学目标",
                                "learning_goal": "理解浮力概念",
                                "html_url": "/uploads/games/demo.html",
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

            docx_bytes = export_service.export_to_docx(plan.id)
            document = Document(BytesIO(docx_bytes))
            extracted_text = "\n".join(paragraph.text for paragraph in document.paragraphs)

            self.assertIn("课堂小游戏", extracted_text)
            self.assertIn("浮力快答", extracted_text)
            self.assertIn("互动页面", extracted_text)


if __name__ == "__main__":
    unittest.main()
