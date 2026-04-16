"""HTTP routes for the knowledge-base module."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id, get_knowledge_service
from ..schemas import (
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
    KnowledgeFileListResponse,
    KnowledgeFileOut,
    KnowledgeFileUpdate,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from ..services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(get_current_user_id)])


@router.post("/upload/document", response_model=KnowledgeFileOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KnowledgeFileOut:
    """Upload a document and index it into the knowledge base."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        payload = await file.read()
        knowledge_file = await service.add_document(user_id, file.filename or "", payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    finally:
        await file.close()

    return KnowledgeFileOut.model_validate(knowledge_file)


@router.post("/upload/image", response_model=KnowledgeFileOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    description: str = Form(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KnowledgeFileOut:
    """Upload an image and index its description."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        payload = await file.read()
        knowledge_file = await service.add_image(user_id, file.filename or "", payload, description)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    finally:
        await file.close()

    return KnowledgeFileOut.model_validate(knowledge_file)


@router.get("/files", response_model=KnowledgeFileListResponse)
def list_files(
    skip: int = 0,
    limit: int = 100,
    file_type: str | None = None,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KnowledgeFileListResponse:
    """List uploaded knowledge files with optional filtering."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        items, total = service.list_files(user_id=user_id, skip=skip, limit=limit, file_type=file_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return KnowledgeFileListResponse(
        items=[KnowledgeFileOut.model_validate(item) for item in items],
        total=total,
    )


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete a knowledge file and its indexed content."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        deleted = await service.delete_file(file_id, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/files/{file_id}", response_model=KnowledgeFileOut)
def update_file(
    file_id: str,
    data: KnowledgeFileUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KnowledgeFileOut:
    """Rename or update knowledge-file description/tags."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        updated = service.update_file(
            file_id,
            filename=data.filename if data.filename is not None else None,
            description=data.description if data.description is not None else None,
            tags=data.tags if data.tags is not None else None,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")
    return KnowledgeFileOut.model_validate(updated)


@router.post("/answer", response_model=KnowledgeAnswerResponse)
async def answer_with_knowledge(
    data: KnowledgeAnswerRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> KnowledgeAnswerResponse:
    """Answer a user question with grounded knowledge-base evidence."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        payload = await service.answer(
            user_id,
            data.query,
            top_k=data.top_k,
            file_type=data.file_type,
            enable_llm_rerank=data.enable_llm_rerank,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return KnowledgeAnswerResponse(**payload)


@router.post("/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(
    data: KnowledgeSearchRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[KnowledgeSearchResult]:
    """Search the knowledge base using semantic retrieval."""
    service: KnowledgeService = get_knowledge_service(db, user_id=user_id)
    try:
        results = await service.search(
            user_id,
            data.query,
            top_k=data.top_k,
            file_type=data.file_type,
            enable_llm_rerank=data.enable_llm_rerank,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return [KnowledgeSearchResult(**item) for item in results]
