"""HTTP routes for the document editor chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import (
    get_current_user_id,
    get_conversation_service,
    get_operation_service,
    get_plan_service,
    get_session_factory,
    get_tool_executor,
    get_tools_registry,
)
from ..schemas import EditorChatRequest
from ..services import ConversationService, DocumentEditor, OperationService, PlanService
from ...tools import ToolExecutor, ToolsRegistry

router = APIRouter(prefix="/api/editor", tags=["editor"], dependencies=[Depends(get_current_user_id)])


@router.post("/chat")
async def chat(
    payload: EditorChatRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    """Stream editor responses as server-sent events."""
    plan_service: PlanService = get_plan_service(db, user_id=user_id)
    conv_service: ConversationService = get_conversation_service(db, user_id=user_id)
    op_service: OperationService = get_operation_service(db, user_id=user_id)
    tools_registry: ToolsRegistry = get_tools_registry()
    tool_executor: ToolExecutor = get_tool_executor()

    editor = DocumentEditor(
        plan_id=payload.plan_id,
        conversation_id=payload.conversation_id,
        plan_service=plan_service,
        conv_service=conv_service,
        op_service=op_service,
        tools_registry=tools_registry,
        tool_executor=tool_executor,
        db=db,
        user_id=user_id,
        db_factory=get_session_factory(),
    )
    return StreamingResponse(
        editor.process_message(payload.message, disconnect_checker=http_request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
