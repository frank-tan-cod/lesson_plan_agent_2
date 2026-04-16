from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks

from backend.app.routers import export as export_router
from backend.app.routers import presentations as presentations_router
from backend.app.schemas import ExportRequest


class _FakeLessonExportService:
    DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    PDF_MEDIA_TYPE = "application/pdf"

    def __init__(self, _plan_service) -> None:
        pass

    def export_to_docx(self, plan_id: str, template: str = "default") -> bytes:
        assert plan_id == "plan-1"
        assert template == "default"
        return b"PK-test-docx"


class _FakePresentationExportService:
    PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def __init__(self, _presentation_service) -> None:
        pass

    def export_to_pptx(self, plan_id: str) -> bytes:
        assert plan_id == "ppt-1"
        return b"PK-test-pptx"


class ExportRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_lesson_export_queues_auto_ingest_in_background(self) -> None:
        background_tasks = BackgroundTasks()

        with patch.object(export_router, "ExportService", _FakeLessonExportService):
            response = await export_router.export_plan(
                ExportRequest(plan_id="plan-1", format="docx", template="default"),
                background_tasks,
                db=object(),
                user_id="user-1",
            )

        self.assertEqual(response.media_type, _FakeLessonExportService.DOCX_MEDIA_TYPE)
        self.assertIn("lesson-plan-plan-1.docx", response.headers["content-disposition"])
        self.assertEqual(len(background_tasks.tasks), 1)
        task = background_tasks.tasks[0]
        self.assertIs(task.func, export_router.auto_ingest_plan_task)
        self.assertEqual(task.args, ("plan-1", "user-1"))
        self.assertEqual(task.kwargs, {"trigger": "export"})

    async def test_presentation_export_queues_auto_ingest_in_background(self) -> None:
        background_tasks = BackgroundTasks()

        with patch.object(
            presentations_router,
            "PresentationExportService",
            _FakePresentationExportService,
        ):
            response = await presentations_router.export_presentation(
                "ppt-1",
                background_tasks,
                db=object(),
                user_id="user-1",
            )

        self.assertEqual(response.media_type, _FakePresentationExportService.PPTX_MEDIA_TYPE)
        self.assertIn("presentation-ppt-1.pptx", response.headers["content-disposition"])
        self.assertEqual(len(background_tasks.tasks), 1)
        task = background_tasks.tasks[0]
        self.assertIs(task.func, presentations_router.auto_ingest_presentation_task)
        self.assertEqual(task.args, ("ppt-1", "user-1"))
        self.assertEqual(task.kwargs, {"trigger": "presentation_export"})


if __name__ == "__main__":
    unittest.main()
