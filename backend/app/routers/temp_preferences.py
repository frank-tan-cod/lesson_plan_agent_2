"""HTTP routes for conversation-scoped temporary preferences."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import TempPreferencesPayload
from ..services.conversation_service import ConversationService

router = APIRouter(prefix="/api/conversations", tags=["temp-preferences"], dependencies=[Depends(get_current_user_id)])


@router.get("/{conv_id}/temp-preferences", response_model=TempPreferencesPayload)
def get_temp_preferences(
    conv_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Fetch temporary preferences for one conversation."""
    service = ConversationService(db, user_id=user_id)
    preferences = service.get_temp_preferences(conv_id)
    if preferences is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return preferences


@router.put("/{conv_id}/temp-preferences", response_model=TempPreferencesPayload)
def replace_temp_preferences(
    conv_id: str,
    data: TempPreferencesPayload = Body(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Replace the temporary preference payload for one conversation."""
    service = ConversationService(db, user_id=user_id)
    conversation = service.replace_temp_preferences(conv_id, data.model_dump(exclude_none=True))
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return (conversation.metadata_json or {}).get("temp_preferences", {})


@router.patch("/{conv_id}/temp-preferences", response_model=TempPreferencesPayload)
def patch_temp_preferences(
    conv_id: str,
    data: TempPreferencesPayload = Body(...),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Merge temporary preference keys into the existing payload."""
    service = ConversationService(db, user_id=user_id)
    conversation = service.patch_temp_preferences(conv_id, data.model_dump(exclude_none=True))
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return (conversation.metadata_json or {}).get("temp_preferences", {})
