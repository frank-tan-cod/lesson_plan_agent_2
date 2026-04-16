"""HTTP routes for savepoint management."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import RestoreResponse, SavepointCreate, SavepointOut
from ..services.knowledge_service import EDITOR_SNAPSHOT_SOURCE, KnowledgeService
from ..services.plan_ingestion import plan_to_markdown
from ..services.plan_service import PlanService
from ..services.savepoint_service import SavepointService

router = APIRouter(prefix="/api/savepoints", tags=["savepoints"], dependencies=[Depends(get_current_user_id)])
logger = logging.getLogger(__name__)


def _serialize_savepoint(savepoint: object, *, include_snapshot: bool) -> SavepointOut:
    """Convert an ORM savepoint into an API schema."""
    payload = SavepointOut.model_validate(savepoint).model_dump()
    if not include_snapshot:
        payload["snapshot"] = None
    return SavepointOut(**payload)


@router.post("", response_model=SavepointOut, status_code=status.HTTP_201_CREATED)
async def create_savepoint(
    data: SavepointCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> SavepointOut:
    """Create a savepoint for a lesson plan."""
    service = SavepointService(db, user_id=user_id)
    try:
        savepoint = service.create(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if data.persist_to_knowledge:
        plan = PlanService(db, user_id=user_id).get(data.plan_id)
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found.")
        knowledge_service = KnowledgeService(db, user_id=user_id)
        title = (data.knowledge_title or "").strip() or f"{plan.title} - {data.label}"
        description = (data.knowledge_description or "").strip() or None
        tags = [str(item).strip() for item in data.knowledge_tags if str(item).strip()]
        markdown = _build_snapshot_markdown(
            plan_title=plan.title,
            doc_type=plan.doc_type,
            snapshot_markdown=plan_to_markdown(plan, content=data.snapshot).strip(),
            snapshot_label=data.label,
            knowledge_title=title,
            description=description,
            tags=tags,
        )
        try:
            knowledge_file = await knowledge_service.add_document(
                user_id,
                _build_snapshot_filename(title, savepoint.id),
                markdown.encode("utf-8"),
                description=description,
                metadata_json={
                    "source": EDITOR_SNAPSHOT_SOURCE,
                    "trigger": "save_to_knowledge",
                    "plan_id": plan.id,
                    "plan_title": plan.title,
                    "doc_type": plan.doc_type,
                    "savepoint_id": savepoint.id,
                    "savepoint_label": savepoint.label,
                    "tags": tags,
                },
            )
            logger.info(
                "编辑器快照已保存到知识库，plan_id=%s savepoint_id=%s knowledge_file_id=%s",
                plan.id,
                savepoint.id,
                knowledge_file.id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("编辑器快照入库失败，plan_id=%s savepoint_id=%s: %s", data.plan_id, savepoint.id, exc)

    return _serialize_savepoint(savepoint, include_snapshot=True)


@router.get("", response_model=list[SavepointOut])
def list_savepoints(
    plan_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[SavepointOut]:
    """List savepoints for a lesson plan."""
    service = SavepointService(db, user_id=user_id)
    savepoints = service.list_by_plan(plan_id)
    return [_serialize_savepoint(item, include_snapshot=False) for item in savepoints]


@router.post("/{savepoint_id}/restore", response_model=RestoreResponse)
def restore_savepoint(
    savepoint_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> RestoreResponse:
    """Restore a lesson plan from a savepoint snapshot."""
    service = SavepointService(db, user_id=user_id)
    try:
        savepoint = service.restore(savepoint_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if savepoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Savepoint not found.")
    return RestoreResponse(status="restored", plan_id=savepoint.plan_id, savepoint_id=savepoint.id)


@router.delete("/{savepoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_savepoint(
    savepoint_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a savepoint."""
    service = SavepointService(db, user_id=user_id)
    deleted = service.delete(savepoint_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Savepoint not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _build_snapshot_filename(title: str, savepoint_id: str) -> str:
    stem = Path(title).stem.strip() or "snapshot"
    return f"{stem}-{savepoint_id[:8]}.md"


def _build_snapshot_markdown(
    *,
    plan_title: str,
    doc_type: str,
    snapshot_markdown: str,
    snapshot_label: str,
    knowledge_title: str,
    description: str | None,
    tags: list[str],
) -> str:
    lines = [
        f"# {knowledge_title}",
        "",
        f"**快照标签**：{snapshot_label}  ",
        f"**来源文档**：{plan_title}  ",
        f"**文档类型**：{'PPT' if doc_type == 'presentation' else '教案'}  ",
        "**来源方式**：编辑器保存进知识库，可作为回退点  ",
    ]
    if tags:
        lines.append(f"**文件标签**：{'、'.join(tags)}  ")
    if description:
        lines.extend(["", "## 快照说明", "", description])
    lines.extend(["", "## 当前文件快照", "", snapshot_markdown])
    return "\n".join(lines).strip() + "\n"
