"""Smoke tests for lesson-plan editing tools."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.schemas import PlanCreate
from backend.app.services.conversation_service import ConversationService
from backend.app.services.operation_service import OperationService
from backend.app.services.plan_service import PlanService
from backend.app.tools.lesson_tools import (
    adjust_duration_tool,
    delete_paragraphs_in_section_tool,
    evaluate_plan_suitability_tool,
    get_section_details_tool,
    insert_section_tool,
    insert_paragraphs_in_section_tool,
    move_section_tool,
    replace_paragraphs_in_section_tool,
    replace_text_in_plan_tool,
    rewrite_section_tool,
)


def main() -> None:
    """Run a lightweight integration smoke test against the local SQLite DB."""
    init_db()

    plan_id: str | None = None
    conversation_id: str | None = None

    try:
        with session_maker() as session:
            plan_service = PlanService(session)
            conversation_service = ConversationService(session)
            plan = plan_service.create(
                PlanCreate(
                    title="工具冒烟测试教案",
                    subject="语文",
                    grade="三年级",
                    content={
                        "sections": [
                            {"type": "导入", "content": "原始导入内容", "duration": 5},
                            {
                                "type": "新授",
                                "content": "第一段：讲授重点知识\n\n第二段：引导学生观察例子",
                                "duration": 20,
                            },
                            {"type": "总结", "content": "课堂总结", "duration": 10},
                        ]
                    },
                )
            )
            conversation = conversation_service.create(plan.id)
            plan_id = plan.id
            conversation_id = conversation.id

        rewrite_result = rewrite_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="导入",
            new_content="新的导入活动：播放图片并提问。",
        )
        assert rewrite_result["ok"] is True

        with session_maker() as session:
            updated_plan = PlanService(session).get(plan_id)
            assert updated_plan is not None
            assert updated_plan.content["sections"][0]["content"] == "新的导入活动：播放图片并提问。"

        adjust_result = adjust_duration_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="导入",
            new_duration=30,
        )
        assert adjust_result["ok"] is False
        assert "超出限制" in adjust_result["message"]

        insert_result = insert_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="练习",
            content="完成两道基础练习题。",
            duration=8,
            position="before",
            reference_section="总结",
        )
        assert insert_result["ok"] is True

        replace_result = replace_text_in_plan_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="新授",
            target_text="重点知识",
            replacement_text="核心概念",
        )
        assert replace_result["ok"] is True
        assert replace_result["replacements"] == 1

        details_result = get_section_details_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="新授",
        )
        assert details_result["ok"] is True
        assert len(details_result["section"]["paragraphs"]) == 2
        assert details_result["section"]["paragraphs"][0]["text"] == "第一段：讲授核心概念"

        paragraph_insert_result = insert_paragraphs_in_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="新授",
            position="after",
            paragraph_index=0,
            new_text="新增段：先让学生口头复述概念。",
        )
        assert paragraph_insert_result["ok"] is True
        assert paragraph_insert_result["inserted_paragraph_index"] == 1

        paragraph_replace_result = replace_paragraphs_in_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="新授",
            start_paragraph_index=1,
            new_text="替换段：通过生活化例子解释概念。",
        )
        assert paragraph_replace_result["ok"] is True

        paragraph_delete_result = delete_paragraphs_in_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="新授",
            start_paragraph_index=2,
        )
        assert paragraph_delete_result["ok"] is True

        move_result = move_section_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            section_type="总结",
            new_index=1,
        )
        assert move_result["ok"] is True

        evaluate_result = evaluate_plan_suitability_tool(
            plan_id=plan_id,
            conversation_id=conversation_id,
            focus="是否适合作为第一课入门",
        )
        assert evaluate_result["ok"] is True
        assert "第一课" in evaluate_result["message"]

        with session_maker() as session:
            unchanged_plan = PlanService(session).get(plan_id)
            assert unchanged_plan is not None
            assert unchanged_plan.content["sections"][0]["duration"] == 5
            assert unchanged_plan.content["sections"][1]["type"] == "总结"
            assert any(section["type"] == "练习" for section in unchanged_plan.content["sections"])
            assert unchanged_plan.content["sections"][2]["content"] == (
                "第一段：讲授核心概念\n\n替换段：通过生活化例子解释概念。"
            )

            operations = OperationService(session).list_by_conversation(conversation_id)
            assert any(item.tool_name == "rewrite_section" for item in operations)
            assert any(item.tool_name == "insert_section" for item in operations)
            assert any(item.tool_name == "get_section_details" for item in operations)
            assert any(item.tool_name == "replace_text_in_plan" for item in operations)
            assert any(item.tool_name == "insert_paragraphs_in_section" for item in operations)
            assert any(item.tool_name == "replace_paragraphs_in_section" for item in operations)
            assert any(item.tool_name == "delete_paragraphs_in_section" for item in operations)
            assert any(item.tool_name == "move_section" for item in operations)
            assert any(item.tool_name == "evaluate_plan_suitability" for item in operations)
            assert not any(item.tool_name == "adjust_duration" and item.result and item.result.get("ok") is False for item in operations)

        print("lesson_tools_smoke passed")
    finally:
        if plan_id:
            with session_maker() as session:
                PlanService(session).delete(plan_id)


if __name__ == "__main__":
    main()
