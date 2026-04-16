from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.database import init_db, session_maker
from backend.app.models import PreferencePreset
from backend.app.schemas import PreferenceCreate, PreferenceUpdate, TempPreferencesPayload
from backend.app.services.preference_service import (
    PreferenceService,
    serialize_preference_preset,
    validate_parse_response,
)
from backend.app.temp_preferences import build_preference_prompt_injection
from backend.app.user_context import DEFAULT_USER_ID


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("LLM response queue is empty.")
        content = self.responses.pop(0)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class PreferenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        with session_maker() as session:
            session.execute(delete(PreferencePreset).where(PreferencePreset.user_id == DEFAULT_USER_ID))
            session.commit()

    def test_create_preset_accepts_structured_preferences(self) -> None:
        with session_maker() as session:
            service = PreferenceService(session, user_id=DEFAULT_USER_ID)
            payload = TempPreferencesPayload(
                teaching_pace="thorough",
                language_style="encouraging",
                other_notes="保留课堂追问。",
            )

            preset = service.create_preset(
                DEFAULT_USER_ID,
                PreferenceCreate(
                    name="讲透并鼓励",
                    description="关键内容展开并保留鼓励式表达。",
                    structured_preferences=payload,
                    tags=["课堂风格"],
                    is_active=True,
                ),
            )

            serialized = serialize_preference_preset(preset)

        expected_prompt = build_preference_prompt_injection(payload.model_dump(exclude_none=True))
        self.assertEqual(preset.prompt_injection, expected_prompt)
        self.assertEqual(
            serialized.structured_preferences.model_dump(exclude_none=True),
            payload.model_dump(exclude_none=True),
        )

    def test_update_preset_rewrites_prompt_from_structured_preferences(self) -> None:
        with session_maker() as session:
            service = PreferenceService(session, user_id=DEFAULT_USER_ID)
            preset = service.create_preset(
                DEFAULT_USER_ID,
                PreferenceCreate(
                    name="旧预设",
                    prompt_injection="整体表达保持专业、准确、相对严谨。",
                    tags=[],
                    is_active=True,
                ),
            )

            updated = service.update_preset(
                preset.id,
                PreferenceUpdate(
                    structured_preferences=TempPreferencesPayload(
                        visual_focus="visual_first",
                        other_notes="适合时保留截图占位。",
                    )
                ),
                user_id=DEFAULT_USER_ID,
            )

        assert updated is not None
        self.assertEqual(
            updated.prompt_injection,
            "如内容适合展示，优先考虑图片、案例图、示意图或截图占位。\n其他要求：适合时保留截图占位。",
        )

    def test_parse_response_backfills_structured_preferences(self) -> None:
        response = validate_parse_response(
            [
                {
                    "name": "口语表达",
                    "description": "整体更贴近真实课堂。",
                    "prompt_injection": "整体表达更自然口语化，贴近真实课堂交流。\n其他要求：多用生活化例子。",
                    "tags": ["表达风格"],
                }
            ]
        )

        self.assertEqual(len(response.suggestions), 1)
        self.assertEqual(
            response.suggestions[0].structured_preferences.model_dump(exclude_none=True),
            {
                "language_style": "conversational",
                "other_notes": "多用生活化例子。",
            },
        )

    def test_parse_natural_language_accepts_structured_only_llm_output(self) -> None:
        payload = {
            "suggestions": [
                {
                    "name": "探究互动课堂",
                    "description": "整体更强调互动与鼓励表达。",
                    "structured_preferences": {
                        "interaction_level": "interactive",
                        "language_style": "encouraging",
                        "other_notes": "多给学生留思考和表达空间。",
                    },
                    "prompt_injection": "",
                    "tags": ["教学策略", "课堂氛围"],
                }
            ]
        }

        with session_maker() as session:
            service = PreferenceService(session, user_id=DEFAULT_USER_ID)
            service._llm_client = FakeLLMClient([json.dumps(payload, ensure_ascii=False)])
            suggestions = asyncio.run(
                service.parse_natural_language("希望课堂多一点互动，并且语气更鼓励，给学生留表达空间。")
            )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(
            suggestions[0]["structured_preferences"],
            {
                "interaction_level": "interactive",
                "language_style": "encouraging",
                "other_notes": "多给学生留思考和表达空间。",
            },
        )
        self.assertEqual(
            suggestions[0]["prompt_injection"],
            "尽量提高互动频率，多安排提问、讨论或学生表达。\n"
            "整体表达带有鼓励和引导感，帮助学生建立参与信心。\n"
            "其他要求：多给学生留思考和表达空间。",
        )

    def test_parse_natural_language_prompt_mentions_structured_preferences_schema(self) -> None:
        with session_maker() as session:
            service = PreferenceService(session, user_id=DEFAULT_USER_ID)
            fake_client = FakeLLMClient([json.dumps({"suggestions": []}, ensure_ascii=False)])
            service._llm_client = fake_client
            asyncio.run(service.parse_natural_language("希望默认更口语化，PPT 适合时多放图例。"))

        system_prompt = fake_client.chat.completions.calls[-1]["messages"][0]["content"]
        self.assertIn("structured_preferences", system_prompt)
        self.assertIn("teaching_pace: compact | balanced | thorough", system_prompt)
        self.assertIn("visual_focus: auto | text_first | visual_first", system_prompt)


if __name__ == "__main__":
    unittest.main()
