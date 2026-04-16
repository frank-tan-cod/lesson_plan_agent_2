"""HTTP routes for the simplified fixed-user profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user_id
from ..models import User
from ..schemas import UserMeOut

router = APIRouter(prefix="/api/user", tags=["user"], dependencies=[Depends(get_current_user_id)])


@router.get("/me", response_model=UserMeOut)
def get_me(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)) -> UserMeOut:
    """Return the authenticated user profile."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return UserMeOut(id=user.id, username=user.username)
