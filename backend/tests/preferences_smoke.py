"""Smoke test for preference presets and temporary preferences."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import delete

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.dependencies import DEFAULT_USER_ID
from backend.app.models import PreferencePreset
from backend.app.schemas import PlanCreate, PreferenceCreate, PreferenceUpdate, TempPreferencesPayload
from backend.app.services.conversation_service import ConversationService
from backend.app.services.plan_service import PlanService
from backend.app.services.preference_service import (
    PreferenceService,
    get_active_preferences_text,
    get_temp_preferences,
)


async def main() -> None:
    """Run a small end-to-end smoke test without calling a real LLM."""
    init_db()

    with session_maker() as session:
        session.execute(delete(PreferencePreset).where(PreferencePreset.user_id == DEFAULT_USER_ID))
        session.commit()

        plan_service = PlanService(session)
        conversation_service = ConversationService(session)
        preference_service = PreferenceService(session)

        plan = plan_service.create(
            PlanCreate(
                title="偏好测试教案",
                subject="数学",
                grade="五年级",
                content={"sections": []},
            )
        )
        conversation = conversation_service.create(plan.id)

        preset = preference_service.create_preset(
            DEFAULT_USER_ID,
            PreferenceCreate(
                name="45分钟课时",
                description="一节课控制在45分钟左右。",
                structured_preferences=TempPreferencesPayload(
                    teaching_pace="compact",
                    other_notes="严格控制总时长为45分钟，误差不超过2分钟。",
                ),
                tags=["课时"],
                is_active=True,
            ),
        )
        preference_service.create_preset(
            DEFAULT_USER_ID,
            PreferenceCreate(
                name="小组讨论优先",
                description="优先安排学生讨论。",
                prompt_injection="优先设计小组讨论环节，每个知识点尽量安排讨论活动。",
                tags=["教学策略"],
                is_active=False,
            ),
        )

        updated = preference_service.update_preset(
            preset.id,
            PreferenceUpdate(description="一节课通常控制在45分钟左右。"),
            user_id=DEFAULT_USER_ID,
        )
        toggled = preference_service.toggle_active(preset.id, user_id=DEFAULT_USER_ID)

        conversation_service.replace_temp_preferences(
            conversation.id,
            {
                "teaching_pace": "balanced",
                "language_style": "conversational",
            },
        )
        conversation_service.patch_temp_preferences(
            conversation.id,
            {
                "interaction_level": "interactive",
                "other_notes": "多用生活化例子。",
            },
        )

        presets = preference_service.get_presets(DEFAULT_USER_ID)
        active_text = get_active_preferences_text(DEFAULT_USER_ID)
        temp_preferences = get_temp_preferences(conversation.id)

    assert updated is not None
    assert updated.description == "一节课通常控制在45分钟左右。"
    assert toggled is not None
    assert toggled.is_active is False
    assert len(presets) >= 2
    assert active_text == ""
    assert temp_preferences == {
        "teaching_pace": "balanced",
        "language_style": "conversational",
        "interaction_level": "interactive",
        "other_notes": "多用生活化例子。",
    }

    print("Preference smoke test passed.")
    print(f"Conversation: {conversation.id}")
    print(f"Preset count: {len(presets)}")
    print(f"Temp preferences: {temp_preferences}")


if __name__ == "__main__":
    asyncio.run(main())
