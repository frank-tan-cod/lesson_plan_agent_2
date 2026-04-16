"""Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .presentation_layouts import get_presentation_template, resolve_template_layout_name
from .presentation_models import (
    body_to_bullets,
    coerce_presentation_bullet_points,
    coerce_optional_positive_int,
    coerce_presentation_text,
    normalize_slide_template,
)


class ORMBaseModel(BaseModel):
    """Base schema that can parse ORM objects."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PlanBase(BaseModel):
    """Shared lesson plan fields."""

    title: str | None = None
    doc_type: Literal["lesson", "presentation"] | None = None
    subject: str | None = None
    grade: str | None = None
    content: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class PlanCreate(PlanBase):
    """Request body for creating a lesson plan."""

    title: str
    requirements: str | None = None
    additional_files: list[str] = Field(default_factory=list)
    course_context: str | None = None


class PlanUpdate(PlanBase):
    """Request body for updating a lesson plan."""


class PlanOut(ORMBaseModel):
    """Lesson plan API response."""

    id: str
    title: str
    doc_type: Literal["lesson", "presentation"]
    subject: str | None = None
    grade: str | None = None
    content: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class PlanListResponse(BaseModel):
    """Paginated lesson plan listing."""

    items: list[PlanOut]
    total: int


class MiniGamePayload(BaseModel):
    """Structured mini-game payload stored in lesson-plan content."""

    id: str = Field(default="")
    template: Literal["single_choice", "true_false", "flip_cards"] = "single_choice"
    title: str = Field(default="")
    description: str = Field(default="")
    source_section: str | None = None
    learning_goal: str | None = None
    html_url: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_fields(self) -> "MiniGamePayload":
        """Trim optional fields and keep ids stable enough for reuse."""
        self.id = coerce_presentation_text(self.id) or ""
        self.title = coerce_presentation_text(self.title)
        self.description = coerce_presentation_text(self.description)
        self.source_section = coerce_presentation_text(self.source_section) or None
        self.learning_goal = coerce_presentation_text(self.learning_goal) or None
        self.html_url = coerce_presentation_text(self.html_url) or None
        self.data = dict(self.data or {})
        return self


class SlidePayload(BaseModel):
    """Structured slide payload stored in presentation content."""

    template: str = "title_body"
    layout: str = Field(default="title_content")
    title: str = Field(default="")
    subtitle: str | None = None
    body: str = Field(default="")
    bullet_points: list[str] = Field(default_factory=list)
    game_index: int | None = None
    link_text: str | None = None
    link_url: str | None = None
    image_description: str | None = None
    image_url: str | None = None
    notes: str | None = None
    source_section: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_nullable_fields(cls, value: Any) -> Any:
        """Tolerate nullable slide fields from generators and editors."""
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        if payload.get("template") is None and payload.get("layout") is not None:
            payload["template"] = payload.get("layout")

        for field_name in ("template", "layout", "title", "body"):
            payload[field_name] = coerce_presentation_text(payload.get(field_name))
        for field_name in ("subtitle", "link_text", "link_url", "image_description", "image_url", "notes", "source_section"):
            if field_name in payload:
                payload[field_name] = coerce_presentation_text(payload.get(field_name)) or None
        if "bullet_points" in payload:
            payload["bullet_points"] = coerce_presentation_bullet_points(payload.get("bullet_points"))
        if "game_index" in payload:
            payload["game_index"] = coerce_optional_positive_int(payload.get("game_index"))
        return payload

    @model_validator(mode="after")
    def normalize_fields(self) -> "SlidePayload":
        """Keep legacy and new slide fields synchronized."""
        self.template = normalize_slide_template(self.template, self.layout)
        self.layout = resolve_template_layout_name(self.template, self.layout)
        self.title = coerce_presentation_text(self.title)
        self.subtitle = coerce_presentation_text(self.subtitle) or None
        self.body = coerce_presentation_text(self.body)
        if self.template == "title_subtitle":
            if not self.subtitle and self.body:
                self.subtitle = self.body
            self.body = ""
            self.bullet_points = []
        else:
            self.bullet_points = coerce_presentation_bullet_points(self.bullet_points)
            if not self.body and self.bullet_points:
                self.body = "\n".join(self.bullet_points)
            if self.body and not self.bullet_points:
                self.bullet_points = body_to_bullets(self.body)
        self.game_index = coerce_optional_positive_int(self.game_index)
        self.link_text = coerce_presentation_text(self.link_text) or None
        self.link_url = coerce_presentation_text(self.link_url) or None
        self.image_description = coerce_presentation_text(self.image_description) or None
        self.image_url = coerce_presentation_text(self.image_url) or None
        if get_presentation_template(self.template).image_box is None:
            self.image_description = None
            self.image_url = None
        self.notes = coerce_presentation_text(self.notes) or None
        self.source_section = coerce_presentation_text(self.source_section) or None
        return self


class PresentationContent(BaseModel):
    """Structured presentation content stored inside the shared plan table."""

    title: str
    classroom_script: str = Field(default="")
    slides: list[SlidePayload] = Field(default_factory=list)


class PresentationBase(BaseModel):
    """Shared presentation project fields."""

    title: str | None = None
    content: PresentationContent | None = None
    metadata: dict[str, Any] | None = None


class PresentationCreate(PresentationBase):
    """Request body for creating a presentation project."""

    title: str


class PresentationUpdate(PresentationBase):
    """Request body for updating a presentation project."""


class PresentationOut(ORMBaseModel):
    """Presentation API response."""

    id: str
    title: str
    doc_type: Literal["presentation"]
    content: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class PresentationListResponse(BaseModel):
    """Paginated presentation listing."""

    items: list[PresentationOut]
    total: int


class KnowledgeFileOut(ORMBaseModel):
    """Knowledge-file API response."""

    id: str
    user_id: str
    filename: str
    file_type: str
    storage_path: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime


class KnowledgeFileUpdate(BaseModel):
    """Editable knowledge-file fields."""

    filename: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class KnowledgeFileListResponse(BaseModel):
    """Paginated knowledge-file listing."""

    items: list[KnowledgeFileOut]
    total: int


class UserCreate(BaseModel):
    """Request body for user registration."""

    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    """Request body for user login."""

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(ORMBaseModel):
    """Public user payload."""

    id: str
    username: str


class Token(BaseModel):
    """JWT login response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class KnowledgeSearchRequest(BaseModel):
    """Request body for semantic knowledge search."""

    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    file_type: str | None = None
    enable_llm_rerank: bool = True


class KnowledgeSearchResult(BaseModel):
    """Single knowledge-search hit after hybrid retrieval and optional semantic organization."""

    file_id: str
    filename: str
    file_type: str
    text_snippet: str
    relevance_score: float
    matched_snippets: list[str] = Field(default_factory=list)
    summary: str | None = None
    match_reason: str | None = None
    source: str | None = None
    trigger: str | None = None
    doc_type: str | None = None
    search_strategy: str | None = None


class KnowledgeAnswerRequest(BaseModel):
    """Request body for grounded question answering over the knowledge base."""

    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    file_type: str | None = None
    enable_llm_rerank: bool = True


class KnowledgeAnswerCitation(BaseModel):
    """One cited knowledge file used to answer the user's question."""

    file_id: str
    filename: str
    file_type: str
    text_snippet: str
    summary: str | None = None
    match_reason: str | None = None
    source: str | None = None
    trigger: str | None = None
    doc_type: str | None = None
    relevance_score: float


class KnowledgeAnswerResponse(BaseModel):
    """Grounded answer plus supporting citations from the knowledge base."""

    answer: str
    citations: list[KnowledgeAnswerCitation] = Field(default_factory=list)
    results: list[KnowledgeSearchResult] = Field(default_factory=list)
    used_llm: bool = False


class PresentationStylePayload(BaseModel):
    """Visual and density preferences for generated/exported presentations."""

    theme: Literal["scholastic_blue", "forest_green", "sunrise_orange"] = "scholastic_blue"
    density: Literal["comfortable", "balanced", "compact"] = "comfortable"
    school_name: str | None = None
    logo_url: str | None = None
    logo_file_id: str | None = None

    @model_validator(mode="after")
    def normalize_fields(self) -> "PresentationStylePayload":
        """Trim optional branding fields."""
        self.school_name = coerce_presentation_text(self.school_name) or None
        self.logo_url = coerce_presentation_text(self.logo_url) or None
        self.logo_file_id = coerce_presentation_text(self.logo_file_id) or None
        return self


class GeneratePresentationRequest(BaseModel):
    """Request body for generating a presentation from a lesson plan."""

    additional_files: list[str] = Field(default_factory=list)
    course_context: str | None = None
    presentation_style: PresentationStylePayload | None = None


class GeneratePresentationResponse(BaseModel):
    """Response body for generate-presentation endpoint."""

    presentation_id: str


class GenerateLessonGamesRequest(BaseModel):
    """Request body for generating mini-games from a lesson plan."""

    game_count: int = Field(default=3, ge=1, le=5)
    templates: list[Literal["single_choice", "true_false", "flip_cards"]] = Field(default_factory=list)
    replace_existing: bool = True


class ConversationCreate(BaseModel):
    """Request body for starting a conversation."""

    plan_id: str


class ConversationUpdate(BaseModel):
    """Fields allowed when updating conversation metadata."""

    summary: str | None = None
    metadata: dict[str, Any] | None = None


class ConversationOut(ORMBaseModel):
    """Conversation API response."""

    id: str
    plan_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str
    summary: str | None = None
    summary_embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")


class OperationCreate(BaseModel):
    """Request body for logging an operation."""

    conversation_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None


class OperationOut(ORMBaseModel):
    """Operation API response."""

    id: str
    conversation_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    created_at: datetime


class AddImagePlaceholderArgs(BaseModel):
    """Arguments for inserting an image placeholder into a plan section."""

    section_type: str = Field(..., min_length=1, description="目标章节类型，例如“导入”“新授”。")
    position: Literal["start", "end", "after_paragraph"] = Field(
        ...,
        description="插入位置：章节开头、结尾或指定段落之后。",
    )
    description: str = Field(..., min_length=1, description="图片描述，会写入 Markdown 占位符。")
    paragraph_index: int | None = Field(default=None, ge=0, description="当 position 为 after_paragraph 时必填。")

    @model_validator(mode="after")
    def validate_paragraph_index(self) -> "AddImagePlaceholderArgs":
        """Require paragraph_index when inserting after a paragraph."""
        if self.position == "after_paragraph" and self.paragraph_index is None:
            raise ValueError("position 为 after_paragraph 时必须提供 paragraph_index。")
        return self


class ReplaceImagePlaceholderResponse(BaseModel):
    """Response body for a successful placeholder replacement."""

    message: str
    plan_id: str
    description: str
    url: str
    file_id: str
    replaced_sections: int


class AskFollowUpArgs(BaseModel):
    """Arguments for asking the user a clarifying follow-up question."""

    question: str = Field(..., min_length=1, description="当信息不足时向用户提出的澄清问题。")
    options: list[str] | None = Field(default=None, description="可选答案列表，便于前端渲染按钮。")


class FollowUpResult(BaseModel):
    """Structured follow-up payload returned by the tool."""

    type: Literal["follow_up"] = "follow_up"
    question: str
    options: list[str] | None = None


class RequestConfirmationArgs(BaseModel):
    """Arguments for asking the user to confirm a destructive operation."""

    operation_description: str = Field(..., min_length=1, description="对即将执行操作的简要描述。")
    proposed_changes: str = Field(..., min_length=1, description="本次修改将造成的主要影响。")
    tool_to_confirm: str = Field(..., min_length=1, description="待确认后实际执行的工具名。")
    tool_args: dict[str, Any] = Field(default_factory=dict, description="待确认工具的参数。")


class SearchKnowledgeArgs(BaseModel):
    """Arguments for searching the user's knowledge base from a tool call."""

    query: str = Field(..., min_length=1, description="搜索关键词或自然语言描述。")
    top_k: int = Field(default=3, ge=1, le=10, description="返回结果数量。")
    file_type: str | None = Field(default=None, description="可选过滤：document 或 image。")


class SearchConversationSummariesArgs(BaseModel):
    """Arguments for searching archived conversation summaries from a tool call."""

    query: str = Field(..., min_length=1, description="搜索关键词或自然语言描述。")
    top_k: int = Field(default=3, ge=1, le=10, description="返回结果数量。")
    exclude_conversation_id: str | None = Field(default=None, description="可选：排除当前会话，避免搜到自己。")


class GetConversationSummaryArgs(BaseModel):
    """Arguments for fetching one conversation summary from a tool call."""

    conversation_id: str = Field(..., min_length=1, description="目标会话 ID。")


class ConfirmationRequest(BaseModel):
    """Structured confirmation payload returned by the tool."""

    type: Literal["confirmation_required"] = "confirmation_required"
    operation_description: str
    proposed_changes: str
    tool_to_confirm: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """Intent-recognition task item for the editor queue."""

    type: Literal["modify", "query", "follow_up", "reply", "confirm", "cancel"]
    tool_name: str | None = None
    target: str | None = None
    action: str | None = None
    proposed_content: str | None = None
    response: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


GoalStatus = Literal["complete", "need_more_steps", "need_follow_up"]


class TaskList(BaseModel):
    """Structured intent-recognition result."""

    goal_status: GoalStatus | None = Field(default=None, description="原始用户目标当前所处状态。")
    goal_status_explicit: bool = Field(default=False, exclude=True)
    tasks: list[Task] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def mark_goal_status_source(cls, data: Any) -> Any:
        """Remember whether goal_status was explicitly provided by the planner."""
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        payload["goal_status_explicit"] = payload.get("goal_status") is not None
        return payload

    @model_validator(mode="after")
    def normalize_goal_status(self) -> "TaskList":
        """Infer goal_status for legacy payloads that only provided tasks."""
        if self.goal_status is None:
            self.goal_status = self._infer_goal_status(self.tasks)
        return self

    @staticmethod
    def _infer_goal_status(tasks: list[Task]) -> GoalStatus:
        """Keep older task-only planner output working without losing intent."""
        if any(task.type == "follow_up" for task in tasks):
            return "need_follow_up"
        if tasks:
            return "need_more_steps"
        return "complete"


class SavepointCreate(BaseModel):
    """Request body for creating a savepoint."""

    plan_id: str
    label: str
    snapshot: dict[str, Any]
    conversation_id: str | None = None
    persist_to_knowledge: bool = False
    knowledge_title: str | None = None
    knowledge_description: str | None = None
    knowledge_tags: list[str] = Field(default_factory=list)


class SavepointOut(ORMBaseModel):
    """Savepoint API response."""

    id: str
    plan_id: str
    conversation_id: str | None = None
    label: str
    snapshot: dict[str, Any] | None = None
    created_at: datetime


class ConversationSearchRequest(BaseModel):
    """Request body for semantic conversation search."""

    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class ConversationSearchResult(BaseModel):
    """Single semantic-search hit for conversations."""

    conversation_id: str
    plan_id: str
    plan_title: str
    summary: str
    relevance_score: float
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: str | None = None


class ConversationSearchResponse(BaseModel):
    """Paginated response for conversation search."""

    items: list[ConversationSearchResult] = Field(default_factory=list)
    total: int


class ConversationSummaryResponse(BaseModel):
    """Summary-generation response payload."""

    conversation_id: str
    summary: str
    indexed: bool


class RestoreRequest(BaseModel):
    """Empty request body for savepoint restore."""


class RestoreResponse(BaseModel):
    """Response body after restoring a savepoint."""

    status: str
    plan_id: str
    savepoint_id: str


class ExportRequest(BaseModel):
    """Request body for lesson-plan export."""

    plan_id: str
    format: Literal["docx", "pdf"] = "docx"
    template: str = "default"


class EditorChatRequest(BaseModel):
    """Request body for document editor chat."""

    plan_id: str
    message: str
    conversation_id: str | None = None


class UserMeOut(BaseModel):
    """Authenticated user profile."""

    id: str
    username: str


class PreferenceBase(BaseModel):
    """Shared preference preset fields."""

    name: str | None = None
    description: str | None = None
    prompt_injection: str | None = None
    structured_preferences: TempPreferencesPayload | None = None
    tags: list[str] | None = None
    is_active: bool | None = None


class PreferenceCreate(PreferenceBase):
    """Request body for creating a preference preset."""

    name: str
    prompt_injection: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_prompt_source(self) -> "PreferenceCreate":
        prompt_text = (self.prompt_injection or "").strip()
        structured = (
            self.structured_preferences.model_dump(exclude_none=True)
            if self.structured_preferences is not None
            else {}
        )
        if not prompt_text and not structured:
            raise ValueError("prompt_injection or structured_preferences is required.")
        return self


class PreferenceUpdate(PreferenceBase):
    """Request body for updating a preference preset."""


class PreferenceOut(ORMBaseModel):
    """Preference preset API response."""

    id: str
    user_id: str
    name: str
    description: str | None = None
    prompt_injection: str
    structured_preferences: TempPreferencesPayload = Field(default_factory=lambda: TempPreferencesPayload())
    tags: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime


class ParseNaturalLanguageRequest(BaseModel):
    """Request body for natural-language preference parsing."""

    natural_language: str = Field(..., min_length=1)


class PreferenceSuggestion(BaseModel):
    """Single structured preference suggestion from the LLM."""

    name: str
    description: str
    prompt_injection: str
    structured_preferences: TempPreferencesPayload = Field(default_factory=lambda: TempPreferencesPayload())
    tags: list[str] = Field(default_factory=list)


class ParseNaturalLanguageResponse(BaseModel):
    """Structured preference suggestions parsed from natural language."""

    suggestions: list[PreferenceSuggestion] = Field(default_factory=list)


class TempPreferencesPayload(BaseModel):
    """Conversation-scoped temporary preferences used during one editing session."""

    teaching_pace: Literal["compact", "balanced", "thorough"] | None = None
    interaction_level: Literal["lecture", "balanced", "interactive"] | None = None
    detail_level: Literal["summary", "balanced", "step_by_step"] | None = None
    language_style: Literal["rigorous", "conversational", "encouraging"] | None = None
    visual_focus: Literal["auto", "text_first", "visual_first"] | None = None
    other_notes: str | None = None
