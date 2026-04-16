"""Smoke test for knowledge auto-ingestion entrypoints on plan/PPT creation flows."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    """Ensure key user-facing creation flows trigger the auto-ingestion hooks."""
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "knowledge_entrypoints.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"

        from backend.app.database import init_db, session_maker
        from backend.app.routers.plans import create_plan, generate_presentation
        from backend.app.schemas import GeneratePresentationRequest, PlanCreate

        init_db()

        with session_maker() as session:
            with patch("backend.app.routers.plans.auto_ingest_plan", new=AsyncMock()) as ingest_plan:
                created = await create_plan(
                    PlanCreate(
                        title="自动入库教案",
                        subject="数学",
                        grade="五年级",
                        content={"sections": [{"title": "导入", "content": "通过分数游戏热身。"}]},
                    ),
                    db=session,
                    user_id="user-1",
                )
                assert created.id
                assert ingest_plan.await_count == 1

        with session_maker() as session:
            with patch("backend.app.routers.plans.generate_presentation_from_plan", return_value="ppt-123"):
                with patch("backend.app.routers.plans.auto_ingest_presentation", new=AsyncMock()) as ingest_presentation:
                    response = await generate_presentation(
                        plan_id="plan-123",
                        data=GeneratePresentationRequest(additional_files=[]),
                        db=session,
                        user_id="user-1",
                    )
                    assert response.presentation_id == "ppt-123"
                    assert ingest_presentation.await_count == 1

        print("Knowledge entrypoints smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
