"""HTTP routes for global preference preset management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import (
    ParseNaturalLanguageRequest,
    ParseNaturalLanguageResponse,
    PreferenceCreate,
    PreferenceOut,
    PreferenceUpdate,
)
from ..services.preference_service import PreferenceService, serialize_preference_preset, validate_parse_response

router = APIRouter(prefix="/api/preferences", tags=["preferences"], dependencies=[Depends(get_current_user_id)])


@router.get("", response_model=list[PreferenceOut])
def list_preferences(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[PreferenceOut]:
    """List preference presets for the fixed current user."""
    service = PreferenceService(db, user_id=user_id)
    presets = service.get_presets(user_id, active_only=False)
    return [serialize_preference_preset(item) for item in presets]


@router.post("", response_model=PreferenceOut, status_code=status.HTTP_201_CREATED)
def create_preference(
    data: PreferenceCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PreferenceOut:
    """Create a new preference preset."""
    service = PreferenceService(db, user_id=user_id)
    preset = service.create_preset(user_id, data)
    return serialize_preference_preset(preset)


@router.post("/parse", response_model=ParseNaturalLanguageResponse)
async def parse_preferences(
    data: ParseNaturalLanguageRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> ParseNaturalLanguageResponse:
    """Parse free-text preference descriptions into structured suggestions."""
    service = PreferenceService(db, user_id=user_id)
    try:
        suggestions = await service.parse_natural_language(data.natural_language)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return validate_parse_response(suggestions)


@router.put("/{preset_id}", response_model=PreferenceOut)
def update_preference(
    preset_id: str,
    data: PreferenceUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PreferenceOut:
    """Update one preference preset."""
    service = PreferenceService(db, user_id=user_id)
    preset = service.update_preset(preset_id, data, user_id=user_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preference preset not found.")
    return serialize_preference_preset(preset)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(
    preset_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Delete one preference preset."""
    service = PreferenceService(db, user_id=user_id)
    deleted = service.delete_preset(preset_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preference preset not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{preset_id}/toggle", response_model=PreferenceOut)
def toggle_preference(
    preset_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> PreferenceOut:
    """Toggle a preference preset between active and inactive."""
    service = PreferenceService(db, user_id=user_id)
    preset = service.toggle_active(preset_id, user_id=user_id)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preference preset not found.")
    return serialize_preference_preset(preset)
