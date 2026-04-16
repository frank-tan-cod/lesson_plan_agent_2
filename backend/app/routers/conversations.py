"""HTTP routes for conversation management."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationSearchRequest,
    ConversationSearchResponse,
    ConversationSearchResult,
    ConversationSummaryResponse,
)
from ..services.conversation_service import ConversationService
from ..services.summary_service import (
    ConversationSummaryService,
    generate_conversation_summary,
    generate_conversation_summary_task,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"], dependencies=[Depends(get_current_user_id)])


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationOut:
    """Start a conversation for a lesson plan."""
    service = ConversationService(db, user_id=user_id)
    try:
        conversation = service.create(data.plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    plan_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[ConversationOut]:
    """List conversations for a lesson plan."""
    service = ConversationService(db, user_id=user_id)
    conversations = service.list_by_plan(plan_id)
    return [ConversationOut.model_validate(item) for item in conversations]


@router.post("/search", response_model=ConversationSearchResponse)
def search_conversations(
    data: ConversationSearchRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationSearchResponse:
    """Search conversations by semantic summary similarity."""
    service = ConversationSummaryService(db, user_id=user_id)
    items = service.search(data.query, top_k=data.top_k)
    return ConversationSearchResponse(
        items=[ConversationSearchResult.model_validate(item) for item in items],
        total=len(items),
    )


@router.post("/{conv_id}/end", response_model=ConversationOut)
def end_conversation(
    conv_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationOut:
    """Archive a conversation."""
    service = ConversationService(db, user_id=user_id)
    conversation = service.end(conv_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    background_tasks.add_task(generate_conversation_summary_task, conv_id, user_id)
    return ConversationOut.model_validate(conversation)


@router.post("/{conv_id}/generate-summary", response_model=ConversationSummaryResponse)
def generate_summary_for_conversation(
    conv_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ConversationSummaryResponse:
    """Generate a summary for one conversation on demand."""
    try:
        result = generate_conversation_summary(conv_id, db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ConversationSummaryResponse(
        conversation_id=result.conversation_id,
        summary=result.summary,
        indexed=result.indexed,
    )
