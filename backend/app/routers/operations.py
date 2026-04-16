"""HTTP routes for operation logging."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..schemas import OperationCreate, OperationOut
from ..services.operation_service import OperationService

router = APIRouter(prefix="/api/operations", tags=["operations"], dependencies=[Depends(get_current_user_id)])


@router.post("", response_model=OperationOut, status_code=status.HTTP_201_CREATED)
def create_operation(
    data: OperationCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> OperationOut:
    """Record a tool execution."""
    service = OperationService(db, user_id=user_id)
    try:
        operation = service.create(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return OperationOut.model_validate(operation)


@router.get("", response_model=list[OperationOut])
def list_operations(
    conversation_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[OperationOut]:
    """List operations for a conversation."""
    service = OperationService(db, user_id=user_id)
    operations = service.list_by_conversation(conversation_id)
    return [OperationOut.model_validate(item) for item in operations]
