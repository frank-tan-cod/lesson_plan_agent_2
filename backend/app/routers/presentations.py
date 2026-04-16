"""HTTP routes for presentation project management."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import PresentationCreate, PresentationListResponse, PresentationOut, PresentationUpdate
from ..services.plan_ingestion import auto_ingest_presentation, auto_ingest_presentation_task
from ..services.export_pptx import (
    PresentationExportService,
    PresentationExportUnavailableError,
    PresentationNotFoundError,
)
from ..services.presentation_service import PresentationService

router = APIRouter(prefix="/api/presentations", tags=["presentations"], dependencies=[Depends(get_current_user_id)])
logger = logging.getLogger(__name__)


@router.post("", response_model=PresentationOut, status_code=status.HTTP_201_CREATED)
async def create_presentation(
    data: PresentationCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PresentationOut:
    """Create a new presentation project."""
    service = PresentationService(db, user_id=user_id)
    presentation = service.create(data)
    try:
        await auto_ingest_presentation(presentation.id, db, user_id=user_id, trigger="presentation_create")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PPT 创建后自动入库失败，presentation_id=%s: %s", presentation.id, exc)
    return PresentationOut.model_validate(presentation)


@router.get("", response_model=PresentationListResponse)
def list_presentations(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PresentationListResponse:
    """List presentation projects."""
    service = PresentationService(db, user_id=user_id)
    items, total = service.list(skip=skip, limit=limit)
    return PresentationListResponse(items=[PresentationOut.model_validate(item) for item in items], total=total)


@router.get("/search", response_model=PresentationListResponse)
def search_presentations(
    q: str,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PresentationListResponse:
    """Search presentation projects by title keyword."""
    service = PresentationService(db, user_id=user_id)
    items, total = service.search(query=q, skip=skip, limit=limit)
    return PresentationListResponse(items=[PresentationOut.model_validate(item) for item in items], total=total)


@router.get("/{plan_id}", response_model=PresentationOut)
def get_presentation(
    plan_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PresentationOut:
    """Fetch a single presentation project by id."""
    service = PresentationService(db, user_id=user_id)
    presentation = service.get(plan_id)
    if presentation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found.")
    return PresentationOut.model_validate(presentation)


@router.put("/{plan_id}", response_model=PresentationOut)
def update_presentation(
    plan_id: str,
    data: PresentationUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PresentationOut:
    """Update a presentation project."""
    service = PresentationService(db, user_id=user_id)
    presentation = service.update(plan_id, data)
    if presentation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found.")
    return PresentationOut.model_validate(presentation)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_presentation(
    plan_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a presentation project."""
    service = PresentationService(db, user_id=user_id)
    deleted = service.delete(plan_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Presentation not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{plan_id}/export")
async def export_presentation(
    plan_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    """Export a presentation project as a downloadable `.pptx` file."""
    service = PresentationService(db, user_id=user_id)
    export_service = PresentationExportService(service)

    try:
        content = export_service.export_to_pptx(plan_id)
    except PresentationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PresentationExportUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    background_tasks.add_task(auto_ingest_presentation_task, plan_id, user_id, trigger="presentation_export")

    filename = f"presentation-{plan_id}.pptx"
    quoted_filename = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename={filename}; filename*=UTF-8''{quoted_filename}"}
    return StreamingResponse(iter([content]), media_type=PresentationExportService.PPTX_MEDIA_TYPE, headers=headers)
