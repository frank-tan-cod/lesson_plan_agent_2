from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.routers.plans import create_plan
from backend.app.schemas import PlanCreate
from backend.app.services.plan_generator import PlanGenerationError, generate_plan_from_requirements


class _FailingCompletions:
    def create(self, **_: object) -> object:
        raise RuntimeError("llm unavailable")


class _FailingClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FailingCompletions())


def test_create_plan_route_returns_502_when_generation_fails() -> None:
    with patch("backend.app.routers.plans.PlanService.create", side_effect=PlanGenerationError("教案初稿生成失败")):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                create_plan(
                    PlanCreate(
                        title="失败教案",
                        subject="数学",
                        grade="五年级",
                        requirements="请生成初稿",
                    ),
                    db=object(),
                    user_id="user-1",
                )
            )

    assert exc_info.value.status_code == 502
    assert "教案初稿生成失败" in exc_info.value.detail


def test_generate_plan_from_requirements_raises_on_llm_failure() -> None:
    with pytest.raises(PlanGenerationError, match="教案初稿生成失败"):
        generate_plan_from_requirements(
            title="失败教案",
            subject="数学",
            grade="五年级",
            requirements="请生成初稿",
            llm_client=_FailingClient(),
        )
