"""HTTP routes for lesson-plan export."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import ExportRequest
from ..services.export_service import ExportService, ExportUnavailableError, PlanNotFoundError
from ..services.plan_ingestion import auto_ingest_plan_task
from ..services.plan_service import PlanService

router = APIRouter(prefix="/api/export", tags=["export"], dependencies=[Depends(get_current_user_id)])


@router.post("")
async def export_plan(
    payload: ExportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    """Export a lesson plan as a downloadable file."""
    plan_service = PlanService(db, user_id=user_id)
    export_service = ExportService(plan_service)

    try:
        if payload.format == "docx":
            content = export_service.export_to_docx(payload.plan_id, template=payload.template)
            filename = f"lesson-plan-{payload.plan_id}.docx"
            media_type = ExportService.DOCX_MEDIA_TYPE
        else:
            content = export_service.export_to_pdf(payload.plan_id, template=payload.template)
            filename = f"lesson-plan-{payload.plan_id}.pdf"
            media_type = ExportService.PDF_MEDIA_TYPE
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ExportUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    background_tasks.add_task(auto_ingest_plan_task, payload.plan_id, user_id, trigger="export")

    quoted_filename = quote(filename)
    headers = {"Content-Disposition": f"attachment; filename={filename}; filename*=UTF-8''{quoted_filename}"}
    return StreamingResponse(iter([content]), media_type=media_type, headers=headers)
