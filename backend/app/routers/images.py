"""HTTP routes for replacing lesson-plan image placeholders."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id, get_knowledge_service
from ..schemas import PlanUpdate, ReplaceImagePlaceholderResponse
from ..services.knowledge_service import KnowledgeService
from ..services.plan_service import PlanService
from ..user_context import resolve_user_id

router = APIRouter(prefix="/api/plans/{plan_id}/images", tags=["images"], dependencies=[Depends(get_current_user_id)])

IMAGE_PLACEHOLDER_TARGET = "upload_needed"


def _build_pending_placeholder(description: str) -> str:
    """Return the Markdown placeholder text before upload."""
    return f"![图片：{description}]({IMAGE_PLACEHOLDER_TARGET})"


def _build_uploaded_image_markdown(description: str, image_url: str) -> str:
    """Return the Markdown image syntax after upload."""
    return f"![{description}]({image_url})"


def _public_image_url(storage_path: str) -> str:
    """Convert a stored file path into the mounted public uploads URL."""
    return f"/uploads/images/{Path(storage_path).name}"


def _resolve_knowledge_service(db: Session, user_id: str) -> KnowledgeService:
    """Build the knowledge service while remaining compatible with older test doubles."""
    resolved_user_id = resolve_user_id(user_id)
    try:
        return get_knowledge_service(db, user_id=resolved_user_id)
    except TypeError:
        service = get_knowledge_service(db)
        setattr(service, "user_id", resolved_user_id)
        setattr(service, "default_user_id", resolved_user_id)
        return service


@router.post("/replace", response_model=ReplaceImagePlaceholderResponse)
async def replace_image_placeholder(
    plan_id: str,
    description: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ReplaceImagePlaceholderResponse:
    """Upload an image and replace matching placeholders in the lesson plan."""
    plan_service = PlanService(db, user_id=user_id)
    knowledge_service = _resolve_knowledge_service(db, user_id)

    plan = plan_service.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="教案不存在。")

    normalized_description = description.strip()
    if not normalized_description:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片描述不能为空。")

    try:
        payload = await file.read()
        knowledge_file = await knowledge_service.add_image(
            user_id,
            file.filename or "",
            payload,
            normalized_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    finally:
        await file.close()

    content = deepcopy(plan.content) if isinstance(plan.content, dict) else {"sections": []}
    sections = content.get("sections")
    if not isinstance(sections, list):
        sections = []
        content["sections"] = sections

    placeholder = _build_pending_placeholder(normalized_description)
    image_url = _public_image_url(knowledge_file.storage_path)
    replacement = _build_uploaded_image_markdown(normalized_description, image_url)

    replaced_sections = 0
    for section in sections:
        if not isinstance(section, dict):
            continue

        section_content = section.get("content")
        if isinstance(section_content, str) and placeholder in section_content:
            section["content"] = section_content.replace(placeholder, replacement)
            replaced_sections += 1

        elements = section.get("elements")
        if isinstance(elements, list):
            for element in elements:
                if not isinstance(element, dict):
                    continue
                if element.get("type") != "image_placeholder":
                    continue
                if str(element.get("description", "")).strip() != normalized_description:
                    continue
                element["image_url"] = image_url
                element["status"] = "uploaded"

    if replaced_sections == 0:
        await knowledge_service.delete_file(knowledge_file.id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到描述为“{normalized_description}”的图片占位符。",
        )

    updated_plan = plan_service.update(plan_id, PlanUpdate(content=content))
    if updated_plan is None:
        await knowledge_service.delete_file(knowledge_file.id, user_id=user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="教案更新失败。")

    return ReplaceImagePlaceholderResponse(
        message="图片已上传并替换占位符。",
        plan_id=plan_id,
        description=normalized_description,
        url=image_url,
        file_id=knowledge_file.id,
        replaced_sections=replaced_sections,
    )
