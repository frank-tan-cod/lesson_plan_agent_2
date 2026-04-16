"""Smoke test for the presentation project workflow."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PresentationCreate
from backend.app.services.export_pptx import PresentationExportService
from backend.app.services.presentation_service import PresentationService
from backend.app.tools.presentation_tools import register_presentation_tools
from backend.tools import ToolExecutor, ToolsRegistry


async def main() -> None:
    """Run a simple end-to-end smoke test for presentation tools and export."""
    init_db()

    with session_maker() as session:
        service = PresentationService(session)
        presentation = service.create(PresentationCreate(title="PPT 冒烟测试"))

        registry = ToolsRegistry()
        register_presentation_tools(registry)
        executor = ToolExecutor(registry)

        await executor.execute(
            "add_slide",
            {
                "plan_id": presentation.id,
                "layout": "title",
                "title": "课程封面",
            },
        )
        await executor.execute(
            "add_slide",
            {
                "plan_id": presentation.id,
                "layout": "title_content",
                "title": "学习目标",
            },
        )
        await executor.execute(
            "set_bullet_points",
            {
                "plan_id": presentation.id,
                "slide_index": 1,
                "points": ["理解本课重点", "完成课堂练习"],
            },
        )
        outline = await executor.execute(
            "get_presentation_outline",
            {
                "plan_id": presentation.id,
                "max_slides": 5,
            },
        )
        details = await executor.execute(
            "get_slide_details",
            {
                "plan_id": presentation.id,
                "slide_index": 1,
            },
        )
        search_result = await executor.execute(
            "search_in_presentation",
            {
                "plan_id": presentation.id,
                "keyword": "课堂练习",
            },
        )

    with session_maker() as session:
        service = PresentationService(session)
        refreshed = service.get(presentation.id)
        export_service = PresentationExportService(service)
        pptx_bytes = export_service.export_to_pptx(presentation.id)

    assert refreshed is not None
    slides = refreshed.content.get("slides", [])
    assert len(slides) == 2
    assert slides[0]["template"] == "title_body"
    assert slides[1]["template"] == "title_body"
    assert slides[1]["bullet_points"] == ["理解本课重点", "完成课堂练习"]
    assert outline["ok"] is True
    assert outline["slides_count"] == 2
    assert "学习目标" in outline["message"]
    assert details["ok"] is True
    assert details["slide"]["title"] == "学习目标"
    assert search_result["ok"] is True
    assert search_result["matches"]
    assert len(pptx_bytes) > 0
    assert pptx_bytes[:2] == b"PK"

    print("Presentation smoke test passed.")
    print(f"Presentation: {presentation.id}")
    print(f"Slides: {json.dumps(slides, ensure_ascii=False)}")
    print(f"PPTX bytes: {len(pptx_bytes)}")


if __name__ == "__main__":
    asyncio.run(main())
