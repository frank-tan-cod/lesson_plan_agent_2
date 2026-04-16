"""Service logic for global and temporary preference management."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..database import session_maker
from ..models import PreferencePreset
from ..schemas import (
    ParseNaturalLanguageResponse,
    PreferenceCreate,
    PreferenceOut,
    PreferenceSuggestion,
    PreferenceUpdate,
)
from ..temp_preferences import (
    build_preference_prompt_injection,
    normalize_temp_preferences_payload,
    parse_preference_prompt_injection,
)
from ..user_context import DEFAULT_USER_ID, resolve_user_id
from .conversation_service import ConversationService

PREFERENCE_PARSE_PROMPT = """你是一个教学偏好分析助手。用户会描述他或她写教案、生成 PPT 时的习惯和偏好。
请将用户描述解析成结构化的偏好建议列表。每个建议都应尽量输出以下字段：
- name：简短名称
- description：对偏好的解释
- structured_preferences：结构化偏好对象，只能使用以下字段
  - teaching_pace: compact | balanced | thorough
  - interaction_level: lecture | balanced | interactive
  - detail_level: summary | balanced | step_by_step
  - language_style: rigorous | conversational | encouraging
  - visual_focus: auto | text_first | visual_first
  - other_notes: 其他无法归入固定枚举但值得保留的要求
- prompt_injection：与 structured_preferences 对齐的自然语言提示词；如果难以准确组织，可留空字符串
- tags：字符串数组，例如 ["课时", "教学策略", "PPT"]

要求：
- 只在用户明确表达或有较强暗示时填写枚举字段，不要臆造偏好。
- 无法映射到固定字段的内容放进 other_notes。
- 不要输出 schema 之外的字段。
- 输出必须是一个 JSON 对象，格式为 {"suggestions": [...]}。
- 如果没有有效偏好，返回 {"suggestions": []}。"""


def _normalize_structured_preferences(value: Any) -> dict[str, Any]:
    """Normalize any structured preference payload into the shared schema."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, dict):
        return {}
    return normalize_temp_preferences_payload(value)


def _resolve_prompt_source(
    *,
    prompt_injection: str | None,
    structured_preferences: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve stored prompt text plus a normalized structured preference payload."""
    structured = _normalize_structured_preferences(structured_preferences)
    if structured:
        prompt_text = build_preference_prompt_injection(structured).strip()
        if prompt_text:
            return prompt_text, structured

    prompt_text = prompt_injection.strip() if isinstance(prompt_injection, str) else ""
    if prompt_text:
        return prompt_text, parse_preference_prompt_injection(prompt_text)

    return "", {}


def serialize_preference_preset(preset: PreferencePreset) -> PreferenceOut:
    """Serialize a stored preference preset with structured fields for the client."""
    return PreferenceOut.model_validate(
        {
            "id": preset.id,
            "user_id": preset.user_id,
            "name": preset.name,
            "description": preset.description,
            "prompt_injection": preset.prompt_injection,
            "structured_preferences": parse_preference_prompt_injection(preset.prompt_injection),
            "tags": preset.tags,
            "is_active": preset.is_active,
            "created_at": preset.created_at,
        }
    )


def normalize_preference_suggestion(item: dict[str, Any]) -> PreferenceSuggestion:
    """Normalize one suggestion payload into the public response schema."""
    prompt_text, structured = _resolve_prompt_source(
        prompt_injection=item.get("prompt_injection"),
        structured_preferences=item.get("structured_preferences"),
    )
    return PreferenceSuggestion.model_validate(
        {
            "name": item.get("name"),
            "description": item.get("description"),
            "prompt_injection": prompt_text,
            "structured_preferences": structured,
            "tags": item.get("tags", []),
        }
    )


class PreferenceService:
    """Encapsulate persistence and parsing logic for preference presets."""

    def __init__(self, db: Session, user_id: str = "default") -> None:
        self.db = db
        self.user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
        self._llm_client: Any | None = None

    def get_presets(self, user_id: str | None = None, active_only: bool = False) -> list[PreferencePreset]:
        """List preference presets for one user."""
        resolved_user_id = resolve_user_id(user_id, self.user_id)
        stmt = select(PreferencePreset).where(PreferencePreset.user_id == resolved_user_id)
        if active_only:
            stmt = stmt.where(PreferencePreset.is_active.is_(True))

        stmt = stmt.order_by(PreferencePreset.is_active.desc(), PreferencePreset.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def create_preset(self, user_id: str | None, data: PreferenceCreate) -> PreferencePreset:
        """Create a new preference preset."""
        prompt_text, _ = _resolve_prompt_source(
            prompt_injection=data.prompt_injection,
            structured_preferences=data.structured_preferences,
        )
        if not prompt_text:
            raise RuntimeError("Preference preset requires non-empty prompt content.")

        payload = data.model_dump(exclude_none=True, exclude={"structured_preferences"})
        payload["prompt_injection"] = prompt_text
        preset = PreferencePreset(user_id=resolve_user_id(user_id, self.user_id), **payload)
        self.db.add(preset)
        return self._save(preset, "Failed to create preference preset.")

    def update_preset(
        self,
        preset_id: str,
        data: PreferenceUpdate,
        *,
        user_id: str | None = None,
    ) -> PreferencePreset | None:
        """Update an existing preference preset."""
        preset = self._get_owned_preset(preset_id, user_id=user_id)
        if preset is None:
            return None

        updates = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"structured_preferences"})
        should_refresh_prompt = "prompt_injection" in data.model_fields_set or "structured_preferences" in data.model_fields_set
        if should_refresh_prompt:
            prompt_text, _ = _resolve_prompt_source(
                prompt_injection=data.prompt_injection,
                structured_preferences=data.structured_preferences,
            )
            if not prompt_text:
                raise RuntimeError("Preference preset requires non-empty prompt content.")
            updates["prompt_injection"] = prompt_text

        for field, value in updates.items():
            setattr(preset, field, value)
        return self._save(preset, "Failed to update preference preset.")

    def delete_preset(self, preset_id: str, *, user_id: str | None = None) -> bool:
        """Delete a preference preset."""
        preset = self._get_owned_preset(preset_id, user_id=user_id)
        if preset is None:
            return False

        self.db.delete(preset)
        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError("Failed to delete preference preset.") from exc
        return True

    def toggle_active(self, preset_id: str, *, user_id: str | None = None) -> PreferencePreset | None:
        """Flip the active state of a preference preset."""
        preset = self._get_owned_preset(preset_id, user_id=user_id)
        if preset is None:
            return None

        preset.is_active = not preset.is_active
        return self._save(preset, "Failed to toggle preference preset.")

    async def parse_natural_language(self, text: str) -> list[dict[str, Any]]:
        """Use the LLM to convert a free-text preference description into suggestions."""
        client = self._get_llm_client()
        settings = self._get_settings()

        response = await client.chat.completions.create(
            model=settings.MODEL_NAME,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": PREFERENCE_PARSE_PROMPT},
                {"role": "user", "content": f"用户描述：{text}"},
            ],
            stream=False,
        )
        content = response.choices[0].message.content or "{}"

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("偏好解析服务返回了无效 JSON。") from exc

        suggestions = payload.get("suggestions", [])
        if not isinstance(suggestions, list):
            raise RuntimeError("偏好解析服务返回格式不正确。")

        normalized: list[dict[str, Any]] = []
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            try:
                suggestion = normalize_preference_suggestion(item)
            except Exception:
                continue
            normalized.append(suggestion.model_dump(exclude_none=True))
        return normalized

    def _get_owned_preset(self, preset_id: str, *, user_id: str | None = None) -> PreferencePreset | None:
        """Fetch a preset and optionally ensure it belongs to the requested user."""
        preset = self.db.get(PreferencePreset, preset_id)
        resolved_user_id = resolve_user_id(user_id, self.user_id)
        if preset is None:
            return None
        if preset.user_id != resolved_user_id:
            return None
        return preset

    def _save(self, preset: PreferencePreset, error_message: str) -> PreferencePreset:
        """Commit the current transaction and refresh the preset."""
        try:
            self.db.commit()
            self.db.refresh(preset)
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise RuntimeError(error_message) from exc
        return preset

    def _get_llm_client(self) -> Any:
        """Build or reuse the async OpenAI-compatible client."""
        if self._llm_client is None:
            from openai import AsyncOpenAI
            from ..core.settings import require_llm_api_key

            settings = self._get_settings()
            self._llm_client = AsyncOpenAI(
                api_key=require_llm_api_key("偏好解析"),
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        return self._llm_client

    @staticmethod
    def _get_settings():
        """Load runtime settings lazily so imports stay lightweight."""
        from ..core.settings import settings

        return settings


def get_active_preferences_text(
    user_id: str,
    *,
    db_factory: sessionmaker[Session] | None = None,
) -> str:
    """Join all active preset prompt injections into one prompt block."""
    resolved_user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
    factory = db_factory or session_maker
    with factory() as session:
        service = PreferenceService(session, user_id=resolved_user_id)
        presets = service.get_presets(resolved_user_id, active_only=True)
        injections = [item.prompt_injection.strip() for item in presets if item.prompt_injection.strip()]
    return "\n".join(injections)


def get_temp_preferences(
    conv_id: str,
    user_id: str = "default",
    *,
    db_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any]:
    """Return the temporary preference object stored on one conversation."""
    resolved_user_id = resolve_user_id(user_id, DEFAULT_USER_ID)
    factory = db_factory or session_maker
    with factory() as session:
        service = ConversationService(session, user_id=resolved_user_id)
        preferences = service.get_temp_preferences(conv_id)
    return preferences or {}


def validate_parse_response(payload: list[dict[str, Any]]) -> ParseNaturalLanguageResponse:
    """Normalize the parse response payload into the public schema."""
    suggestions = [normalize_preference_suggestion(item) for item in payload]
    return ParseNaturalLanguageResponse(suggestions=suggestions)
