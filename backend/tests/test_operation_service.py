from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import OperationCreate, PlanCreate
from backend.app.services.conversation_service import ConversationService
from backend.app.services.operation_service import OperationService
from backend.app.services.plan_service import PlanService
from backend.tools.cancellation import CancellationToken


def test_create_operation_serializes_non_json_arguments() -> None:
    init_db()

    with session_maker() as session:
        plan = PlanService(session).create(
            PlanCreate(
                title="操作记录序列化测试",
                subject="语文",
                grade="三年级",
                content={"sections": []},
            )
        )
        conversation = ConversationService(session).create(plan.id)
        operation = OperationService(session).create(
            OperationCreate(
                conversation_id=conversation.id,
                tool_name="replace_text_in_plan",
                arguments={
                    "plan_id": plan.id,
                    "target_text": "旧文本",
                    "replacement_text": "新文本",
                    "cancel_token": CancellationToken(),
                },
                result={"ok": True, "message": "已替换 1 处文本。"},
            )
        )

        assert operation.tool_name == "replace_text_in_plan"
        assert operation.arguments["target_text"] == "旧文本"
        assert isinstance(operation.arguments["cancel_token"], dict)
