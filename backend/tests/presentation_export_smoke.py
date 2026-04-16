"""Smoke test for classroom presentation PPTX export."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    """Verify registered layouts can be mixed and exported with auto pagination."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "presentation_export.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"

        from backend.app.database import init_db, session_maker
        from backend.app.presentation_layouts import (
            BoxSpec,
            PaginationSpec,
            PresentationTemplateSpec,
            register_presentation_template,
        )
        from backend.app.schemas import PresentationCreate
        from backend.app.services.export_pptx import export_to_pptx
        from backend.app.services.presentation_service import PresentationService

        init_db()

        register_presentation_template(
            PresentationTemplateSpec(
                name="title_body_wide",
                pptx_layout="title_content",
                pagination=PaginationSpec(chars_per_line=32, max_lines=13),
                body_box=BoxSpec(left=0.65, top=1.95, width=8.7, height=4.95),
            )
        )

        long_body = "\n".join(
            [
                "观察现象：木块会浮起，铁块会下沉。",
                "提出问题：物体在水中为什么会受到向上的力？",
                "实验记录：改变物体体积和浸入深度，比较弹簧测力计示数变化。",
                "结论归纳：浮力方向总是竖直向上。",
                "进一步追问：浮力大小和哪些因素有关？",
                "学生口头总结：排开液体越多，浮力通常越大。",
                "迁移应用：解释游泳圈、潜水艇和热气球中的相似思想。",
            ]
        )

        with session_maker() as session:
            presentation = PresentationService(session, user_id="user-1").create(
                PresentationCreate(
                    title="浮力课堂展示",
                    content={
                        "title": "浮力课堂展示",
                        "classroom_script": long_body,
                        "slides": [
                            {
                                "template": "title_body_wide",
                                "title": "课堂主问题",
                                "body": "物体在液体中为什么会受到向上的力？\n先说猜想，再用实验验证。",
                                "notes": "先让学生说，再追问依据。",
                            },
                            {
                                "template": "title_body_image",
                                "title": "浮力现象观察",
                                "body": long_body,
                                "image_description": "水中木块与铁块对比图",
                                "notes": "提醒学生先说现象，再猜测原因。",
                            }
                        ],
                    },
                )
            )

        payload = {
            "title": presentation.title,
            "classroom_script": presentation.content.get("classroom_script", ""),
            "slides": presentation.content.get("slides", []),
        }
        pptx_bytes = export_to_pptx(payload)

        with NamedTemporaryFile(suffix=".pptx", delete=False) as temp_file:
            temp_file.write(pptx_bytes)
            output_path = Path(temp_file.name)

        assert output_path.stat().st_size > 0
        assert pptx_bytes[:2] == b"PK"
        from pptx import Presentation as PPTXPresentation

        exported = PPTXPresentation(str(output_path))
        assert len(exported.slides) >= 2

        print("Presentation export smoke test passed.")
        print(f"PPTX path: {output_path}")


if __name__ == "__main__":
    main()
