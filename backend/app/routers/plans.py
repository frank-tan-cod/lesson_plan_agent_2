"""HTTP routes for lesson plan management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import (
    GenerateLessonGamesRequest,
    GeneratePresentationRequest,
    GeneratePresentationResponse,
    PlanCreate,
    PlanListResponse,
    PlanOut,
    PlanUpdate,
)
from ..services.plan_ingestion import auto_ingest_plan, auto_ingest_presentation
from ..services.plan_generator import PlanGenerationError
from ..services.plan_service import PlanService
from ..services.game_service import GameGenerationError, generate_games_for_plan
from ..services.presentation_generator import generate_presentation_from_plan

router = APIRouter(prefix="/api/plans", tags=["plans"], dependencies=[Depends(get_current_user_id)])
logger = logging.getLogger(__name__)


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(
    data: PlanCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PlanOut:
    """Create a new lesson plan, optionally from free-form requirements."""
    service = PlanService(db, user_id=user_id)
    try:
        plan = service.create(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PlanGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    try:
        await auto_ingest_plan(plan.id, db, user_id=user_id, trigger="create")
    except Exception as exc:  # noqa: BLE001
        logger.warning("新建文档自动入库失败，plan_id=%s: %s", plan.id, exc)
    return PlanOut.model_validate(plan)


@router.get("", response_model=PlanListResponse)
def list_plans(
    skip: int = 0,
    limit: int = 100,
    subject: str | None = None,
    grade: str | None = None,
    doc_type: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PlanListResponse:
    """List lesson plans with optional filters."""
    service = PlanService(db, user_id=user_id)
    items, total = service.list(skip=skip, limit=limit, subject=subject, grade=grade, doc_type=doc_type)
    return PlanListResponse(items=[PlanOut.model_validate(item) for item in items], total=total)


@router.get("/search", response_model=PlanListResponse)
def search_plans(
    q: str,
    skip: int = 0,
    limit: int = 100,
    doc_type: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PlanListResponse:
    """Search lesson plans by title keyword."""
    service = PlanService(db, user_id=user_id)
    items, total = service.search(query=q, skip=skip, limit=limit, doc_type=doc_type)
    return PlanListResponse(items=[PlanOut.model_validate(item) for item in items], total=total)


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> PlanOut:
    """Fetch a single lesson plan by id."""
    service = PlanService(db, user_id=user_id)
    plan = service.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")
    return PlanOut.model_validate(plan)


@router.put("/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: str,
    data: PlanUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PlanOut:
    """Update a lesson plan."""
    service = PlanService(db, user_id=user_id)
    plan = service.update(plan_id, data)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")
    return PlanOut.model_validate(plan)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(plan_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> Response:
    """Delete a lesson plan."""
    service = PlanService(db, user_id=user_id)
    deleted = service.delete(plan_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{plan_id}/generate-presentation", response_model=GeneratePresentationResponse)
async def generate_presentation(
    plan_id: str,
    data: GeneratePresentationRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> GeneratePresentationResponse:
    """Generate a presentation project from a lesson plan."""
    try:
        presentation_id = generate_presentation_from_plan(
            plan_id=plan_id,
            request=data,
            db_session=db,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to generate presentation for plan %s.", plan_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="生成失败，请稍后重试。") from exc

    try:
        await auto_ingest_presentation(presentation_id, db, user_id=user_id, trigger="generate_presentation")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PPT 初稿自动入库失败，presentation_id=%s: %s", presentation_id, exc)

    return GeneratePresentationResponse(presentation_id=presentation_id)


@router.post("/{plan_id}/generate-games", response_model=PlanOut)
async def generate_lesson_games(
    plan_id: str,
    data: GenerateLessonGamesRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PlanOut:
    """Generate structured mini-games for a lesson plan and persist them into content."""
    service = PlanService(db, user_id=user_id)
    plan = service.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")
    if plan.doc_type != "lesson":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只能为教案生成小游戏。")

    try:
        games = generate_games_for_plan(plan, data)
    except GameGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to generate mini games for plan %s.", plan_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="小游戏生成失败，请稍后重试。") from exc

    content = dict(plan.content) if isinstance(plan.content, dict) else {"sections": []}
    existing = content.get("games")
    if not isinstance(existing, list) or data.replace_existing:
        content["games"] = games
    else:
        content["games"] = [*existing, *games]

    updated = service.update(plan_id, PlanUpdate(content=content))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")

    try:
        await auto_ingest_plan(updated.id, db, user_id=user_id, trigger="generate_games")
    except Exception as exc:  # noqa: BLE001
        logger.warning("小游戏生成后自动入库失败，plan_id=%s: %s", updated.id, exc)

    return PlanOut.model_validate(updated)
