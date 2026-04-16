"""Document editor orchestration around the LLM and tools."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..core.settings import settings

from ..database import session_maker
from ..models import Conversation, Operation, Plan
from ..schemas import OperationCreate, PlanUpdate, Task, TaskList
from ..temp_preferences import render_temp_preferences_text
from ..services.conversation_service import ConversationService
from ..services.editor_guardrails import EditorGuardrails
from ..services.editor_planner import EditorPlanner, PlannerPromptContext
from ..services.editor_runtime import conversation_execution_registry
from ..services.editor_state_store import EditorStateStore
from ..services.operation_service import OperationService
from ..services.plan_service import PlanService
from ..services.preference_service import PreferenceService
from ..tools.control_flow_tools import REQUEST_CONFIRMATION_TOOL_NAME
from ...tools.cancellation import CancellationToken, ToolCancelledError
from ...tools import ToolExecutionError, ToolNotFoundError, ToolValidationError, ToolExecutor, ToolsRegistry

RECENT_TURNS_KEY = "recent_turns"
RECENT_TURNS_LIMIT = 6
RECENT_TURN_CHARS = 240
MAX_SECTION_CONTEXT_CHARS = 900
MAX_CONTEXT_SECTIONS = 3
SECTION_INSERT_POSITION_ALIASES = {
    "start": "start",
    "开头": "start",
    "开始": "start",
    "最前面": "start",
    "前部": "start",
    "end": "end",
    "结尾": "end",
    "末尾": "end",
    "最后": "end",
    "尾部": "end",
    "before": "before",
    "之前": "before",
    "前": "before",
    "after": "after",
    "之后": "after",
    "后": "after",
}
ELEMENT_INSERT_POSITION_ALIASES = {
    "start": "start",
    "开头": "start",
    "开始": "start",
    "最前面": "start",
    "前部": "start",
    "end": "end",
    "结尾": "end",
    "末尾": "end",
    "最后": "end",
    "尾部": "end",
    "after_paragraph": "after_paragraph",
    "首段后": "after_paragraph",
    "首段之后": "after_paragraph",
    "第一段后": "after_paragraph",
    "第一段之后": "after_paragraph",
}
QUOTE_PATTERN = re.compile(r"[\"“”‘’']([^\"“”‘’'\n]{2,160})[\"“”‘’']")


class DocumentEditor:
    """Coordinate chat messages, tool execution, and lesson plan updates."""

    def __init__(
        self,
        plan_id: str,
        conversation_id: str | None,
        plan_service: PlanService,
        conv_service: ConversationService,
        op_service: OperationService,
        tools_registry: ToolsRegistry,
        tool_executor: ToolExecutor,
        db: Session,
        user_id: str = "default",
        db_factory: sessionmaker[Session] | None = None,
        llm_client: Any | None = None,
        history_limit: int = 10,
        max_rounds: int = 6,
    ) -> None:
        self.plan_id = plan_id
        self.conversation_id = conversation_id
        self.plan_service = plan_service
        self.conv_service = conv_service
        self.op_service = op_service
        self.tools_registry = tools_registry
        self.tool_executor = tool_executor
        self.db = db
        self.user_id = user_id
        self.db_factory = db_factory or session_maker
        self.llm_client = llm_client
        self.history_limit = history_limit
        self.max_rounds = max_rounds
        self.cancel_token: CancellationToken | None = None
        self.planner = EditorPlanner(
            json_ready=self._json_ready,
            truncate_text=self._truncate_text,
        )
        self.guardrails = EditorGuardrails(
            self.tools_registry,
            json_ready=self._json_ready,
            clean_text=self._clean_text,
        )
        self.state_store = EditorStateStore(
            conv_service=self.conv_service,
            run_db=self._run_db,
            json_ready=self._json_ready,
            truncate_text=self._truncate_text,
            recent_turn_key=RECENT_TURNS_KEY,
            recent_turn_limit=RECENT_TURNS_LIMIT,
            recent_turn_chars=RECENT_TURN_CHARS,
        )

    async def process_message(
        self,
        user_message: str,
        *,
        disconnect_checker: Callable[[], Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Process a user message and stream SSE events."""
        disconnect_task: asyncio.Task[None] | None = None
        conversation_id: str | None = None
        request_token = CancellationToken()
        self.cancel_token = request_token
        try:
            conversation = await self._ensure_conversation()
            conversation_id = conversation.id
            await conversation_execution_registry.acquire(conversation_id, request_token)
            disconnect_task = self._start_disconnect_watcher(disconnect_checker, request_token)
            self._raise_if_cancelled()
            yield self._format_sse("conversation", {"conversation_id": conversation.id})

            plan = await self._get_plan()
            self._raise_if_cancelled()
            if plan is None:
                yield self._format_sse("error", {"message": self._get_missing_plan_message()})
                return

            recent_ops = await self._list_recent_operations(conversation.id)
            self._raise_if_cancelled()
            normalized_message = user_message.strip()
            await self._append_recent_turn(conversation.id, "user", normalized_message)
            pending_tasks = self._get_pending_tasks(conversation)
            pending_follow_up = self._get_pending_follow_up(conversation)
            root_user_message = self._get_root_user_message(normalized_message, pending_follow_up)

            if pending_tasks and normalized_message not in {"/confirm", "/cancel"}:
                reminder = "当前有待确认的操作，请先发送 /confirm 执行，或发送 /cancel 取消。"
                await self._append_recent_turn(conversation.id, "assistant", reminder, kind="reminder")
                for chunk in self._chunk_text(reminder):
                    yield self._format_sse("delta", {"content": chunk})
                yield self._format_sse(
                    "done",
                    {
                        "conversation_id": conversation.id,
                        "reply": reminder,
                        "plan": self._serialize_plan(plan),
                    },
                )
                return

            if normalized_message == "/cancel":
                await self._clear_pending_tasks(conversation.id)
                await self._clear_pending_confirmation(conversation.id)
                await self._clear_pending_follow_up(conversation.id)
                final_reply = "已取消所有待办操作。"
                await self._append_recent_turn(conversation.id, "assistant", final_reply, kind="reply")
                for chunk in self._chunk_text(final_reply):
                    yield self._format_sse("delta", {"content": chunk})
                yield self._format_sse(
                    "done",
                    {
                        "conversation_id": conversation.id,
                        "reply": final_reply,
                        "plan": self._serialize_plan(plan),
                    },
                )
                return

            if normalized_message == "/confirm":
                if not pending_tasks:
                    final_reply = "没有待执行的任务。"
                    await self._append_recent_turn(conversation.id, "assistant", final_reply, kind="reply")
                    for chunk in self._chunk_text(final_reply):
                        yield self._format_sse("delta", {"content": chunk})
                    yield self._format_sse(
                        "done",
                        {
                            "conversation_id": conversation.id,
                            "reply": final_reply,
                            "plan": self._serialize_plan(plan),
                        },
                    )
                    return

                yield self._format_sse("status", {"content": "正在执行已确认的操作..."})
                await self._clear_pending_confirmation(conversation.id)
                tool_events, response_texts, remaining_tasks, follow_up_payload, confirmation_payload = (
                    await self._process_task_queue(
                        conversation.id,
                        pending_tasks,
                        modify_execution_budget=None,
                    )
                )
            else:
                yield self._format_sse("status", {"content": "正在分析你的需求..."})
                task_plan = await self._recognize_intent(
                    plan,
                    conversation,
                    recent_ops,
                    normalized_message,
                    pending_follow_up=pending_follow_up,
                )
                if task_plan is None or (
                    task_plan.goal_status == "need_more_steps" and not task_plan.tasks
                ):
                    follow_up_payload = self._build_intent_failure_follow_up(
                        plan,
                        conversation,
                        recent_ops,
                        normalized_message,
                        pending_follow_up=pending_follow_up,
                    )
                    await self._clear_pending_tasks(conversation.id)
                    await self._clear_pending_confirmation(conversation.id)
                    resumable_follow_up = self._decorate_follow_up_payload(
                        follow_up_payload,
                        previous_user_message=normalized_message,
                        root_user_message=root_user_message,
                        completed_steps=[],
                        remaining_tasks=self._load_tasks_from_payload(
                            pending_follow_up.get("remaining_tasks") if pending_follow_up else None
                        ),
                    )
                    await self._save_pending_follow_up(conversation.id, resumable_follow_up)
                    await self._append_recent_turn(
                        conversation.id,
                        "assistant",
                        resumable_follow_up.get("question", ""),
                        kind="follow_up",
                    )
                    yield self._format_sse(
                        "follow_up",
                        {
                            "conversation_id": conversation.id,
                            **resumable_follow_up,
                        },
                    )
                    return
                if task_plan.goal_status == "need_follow_up":
                    follow_up_payload = self._build_follow_up_payload_from_task_plan(
                        task_plan,
                        fallback_question="请补充更具体的修改要求。",
                    )
                    await self._clear_pending_tasks(conversation.id)
                    await self._clear_pending_confirmation(conversation.id)
                    resumable_follow_up = self._decorate_follow_up_payload(
                        follow_up_payload,
                        previous_user_message=normalized_message,
                        root_user_message=root_user_message,
                        completed_steps=[],
                        remaining_tasks=self._load_tasks_from_payload(
                            pending_follow_up.get("remaining_tasks") if pending_follow_up else None
                        ),
                    )
                    await self._save_pending_follow_up(conversation.id, resumable_follow_up)
                    await self._append_recent_turn(
                        conversation.id,
                        "assistant",
                        resumable_follow_up.get("question", ""),
                        kind="follow_up",
                    )
                    yield self._format_sse(
                        "follow_up",
                        {
                            "conversation_id": conversation.id,
                            **resumable_follow_up,
                        },
                    )
                    return

                await self._clear_pending_confirmation(conversation.id)
                if pending_follow_up is not None:
                    await self._clear_pending_follow_up(conversation.id)
                quality_context = self._build_content_quality_context(
                    normalized_message,
                    pending_follow_up=pending_follow_up,
                )
                planned_tasks = task_plan.tasks
                current_goal_status = task_plan.goal_status
                current_goal_status_explicit = task_plan.goal_status_explicit
                round_index = 0
                all_tool_events: list[str] = []
                response_texts = []
                remaining_tasks: list[Task] = []
                follow_up_payload = None
                confirmation_payload = None

                while round_index < self.max_rounds:
                    if current_goal_status == "complete" and not planned_tasks:
                        break
                    if current_goal_status == "need_follow_up":
                        follow_up_payload = self._build_follow_up_payload_from_task_plan(
                            TaskList(goal_status=current_goal_status, tasks=planned_tasks),
                            fallback_question="为了继续完成这个需求，请补充更具体的信息。",
                        )
                        break

                    await self._save_pending_tasks(conversation.id, planned_tasks)
                    self._raise_if_cancelled()
                    status_text = "正在整理任务并准备执行..." if round_index == 0 else "正在根据已定位内容继续完成处理..."
                    yield self._format_sse("status", {"content": status_text})

                    round_tool_events, round_response_texts, remaining_tasks, follow_up_payload, confirmation_payload = (
                        await self._process_task_queue(
                            conversation.id,
                            planned_tasks,
                            modify_execution_budget=self._get_initial_modify_execution_budget(
                                planned_tasks,
                                pending_follow_up=pending_follow_up,
                            ),
                            quality_context=quality_context,
                        )
                    )
                    all_tool_events.extend(round_tool_events)
                    response_texts.extend(round_response_texts)

                    if follow_up_payload is not None or confirmation_payload is not None or remaining_tasks:
                        break
                    if current_goal_status == "complete":
                        break
                    if not round_tool_events:
                        break
                    if not current_goal_status_explicit:
                        break

                    round_index += 1
                    if round_index >= self.max_rounds:
                        break

                    latest_plan = await self._get_plan()
                    latest_conversation = await self._ensure_conversation()
                    latest_ops = await self._list_recent_operations(conversation.id)
                    self._raise_if_cancelled()
                    next_task_plan = await self._plan_next_round_after_execution(
                        latest_plan or plan,
                        latest_conversation,
                        latest_ops,
                        normalized_message,
                        completed_steps=response_texts,
                    )
                    if next_task_plan is None:
                        break
                    current_goal_status = next_task_plan.goal_status
                    current_goal_status_explicit = next_task_plan.goal_status_explicit
                    if current_goal_status == "need_follow_up":
                        follow_up_payload = self._build_follow_up_payload_from_task_plan(
                            next_task_plan,
                            fallback_question="为了继续完成这个需求，请补充更具体的信息。",
                        )
                        remaining_tasks = []
                        break
                    if current_goal_status == "complete" and not next_task_plan.tasks:
                        planned_tasks = []
                        break
                    if current_goal_status == "need_more_steps" and not next_task_plan.tasks:
                        break
                    planned_tasks = next_task_plan.tasks

                tool_events = all_tool_events

            for event in tool_events:
                yield event

            if response_texts:
                response_body = "\n\n".join(text for text in response_texts if text.strip())
                for chunk in self._chunk_text(response_body):
                    yield self._format_sse("delta", {"content": chunk})

            if follow_up_payload is not None:
                await self._clear_pending_tasks(conversation.id)
                resumable_follow_up = self._decorate_follow_up_payload(
                    follow_up_payload,
                    previous_user_message=normalized_message,
                    root_user_message=root_user_message,
                    completed_steps=response_texts,
                    remaining_tasks=remaining_tasks,
                )
                await self._save_pending_follow_up(conversation.id, resumable_follow_up)
                await self._append_recent_turn(
                    conversation.id,
                    "assistant",
                    resumable_follow_up.get("question", ""),
                    kind="follow_up",
                )
                yield self._format_sse(
                    "follow_up",
                    {
                        "conversation_id": conversation.id,
                        **resumable_follow_up,
                    },
                )
                return

            if confirmation_payload is not None:
                await self._save_pending_tasks(conversation.id, remaining_tasks)
                await self._save_pending_confirmation(conversation.id, confirmation_payload)
                await self._clear_pending_follow_up(conversation.id)
                await self._append_recent_turn(
                    conversation.id,
                    "assistant",
                    "\n".join(
                        item
                        for item in [
                            confirmation_payload.get("operation_description", ""),
                            confirmation_payload.get("proposed_changes", ""),
                        ]
                        if item
                    ),
                    kind="confirmation",
                )
                yield self._format_sse(
                    "confirmation_required",
                    {
                        "conversation_id": conversation.id,
                        **confirmation_payload,
                    },
                )
                return

            await self._clear_pending_tasks(conversation.id)
            await self._clear_pending_confirmation(conversation.id)
            updated_plan = await self._get_plan()
            self._raise_if_cancelled()
            final_reply = self._build_final_reply(response_texts)
            await self._append_recent_turn(conversation.id, "assistant", final_reply, kind="reply")
            yield self._format_sse(
                "done",
                {
                    "conversation_id": conversation.id,
                    "reply": final_reply,
                    "plan": self._serialize_plan(updated_plan),
                },
            )
        except ToolCancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            if self.conversation_id:
                await self._append_recent_turn(
                    self.conversation_id,
                    "assistant",
                    f"编辑器处理失败：{exc}",
                    kind="error",
                )
            yield self._format_sse("error", {"message": f"编辑器处理失败：{exc}"})
        finally:
            request_token.cancel()
            if disconnect_task is not None:
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass
            if conversation_id is not None:
                await conversation_execution_registry.release(conversation_id, request_token)
            if self.cancel_token is request_token:
                self.cancel_token = None

    def _get_llm_client(self) -> Any:
        """Build or reuse the async OpenAI-compatible client."""
        if self.llm_client is None:
            from openai import AsyncOpenAI
            from ..core.settings import require_llm_api_key

            self.llm_client = AsyncOpenAI(
                api_key=require_llm_api_key("编辑器"),
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        return self.llm_client

    async def _ensure_conversation(self) -> Conversation:
        """Create a conversation when the request does not provide one."""
        if self.conversation_id:
            conversation = await self._run_db(lambda _, conv_service, __: conv_service.get(self.conversation_id))
            if conversation is None:
                raise ValueError("Conversation not found.")
            return conversation

        conversation = await self._run_db(lambda _, conv_service, __: conv_service.create(self.plan_id))
        self.conversation_id = conversation.id
        return conversation

    async def _get_plan(self) -> Plan | None:
        """Load the current lesson plan."""
        return await self._run_db(lambda plan_service, __, ___: plan_service.get(self.plan_id))

    async def _list_recent_operations(self, conversation_id: str) -> list[Operation]:
        """Load recent operation history for prompt construction."""
        return await self._run_db(
            lambda _, __, op_service: op_service.list_by_conversation(conversation_id, limit=self.history_limit)
        )

    async def _record_operation(self, conversation_id: str, tool_name: str, arguments: dict[str, Any], result: Any) -> None:
        """Persist tool execution details."""
        self._raise_if_cancelled()
        payload = OperationCreate(
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments=arguments,
            result=self._json_ready(result),
        )
        await self._run_db(lambda _, __, op_service: op_service.create(payload))

    async def _apply_plan_updates(self, result: Any) -> None:
        """Persist plan changes when a tool returns updated lesson-plan content."""
        if not isinstance(result, dict):
            return

        updated_content = result.get("updated_content")
        if updated_content is None:
            return

        self._raise_if_cancelled()
        await self._run_db(
            lambda plan_service, __, ___: plan_service.update(self.plan_id, PlanUpdate(content=updated_content))
        )

    async def _execute_tool(self, conversation_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool, record it, and normalize the result for the LLM."""
        execution_arguments = dict(arguments)
        execution_arguments["plan_id"] = self.plan_id
        execution_arguments["conversation_id"] = conversation_id
        execution_arguments["user_id"] = self.user_id
        execution_arguments["cancel_token"] = self.cancel_token

        try:
            self._raise_if_cancelled()
            result = await self.tool_executor.execute(tool_name, execution_arguments)
            self._raise_if_cancelled()
            operation_logged = isinstance(result, dict) and bool(result.pop("_operation_logged", False))
            await self._apply_plan_updates(result)
            normalized = self._normalize_tool_result(result)
        except ToolCancelledError:
            raise
        except (ToolNotFoundError, ToolValidationError, ToolExecutionError) as exc:
            operation_logged = False
            normalized = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            operation_logged = False
            normalized = {"ok": False, "error": f"Unexpected tool failure: {exc}"}
        finally:
            # Some tool functions commit changes through their own sessions.
            # Refresh the request-scoped entities so later reads observe the latest state
            # without expiring unrelated ORM instances that callers may still hold.
            self._refresh_request_state(conversation_id)

        if not operation_logged:
            await self._record_operation(conversation_id, tool_name, arguments, normalized)
        return normalized

    async def _recognize_intent(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> TaskList | None:
        """Analyze the user message into an ordered task queue."""
        if user_message == "/confirm":
            return TaskList(goal_status="need_more_steps", tasks=[Task(type="confirm")])
        if user_message == "/cancel":
            return TaskList(goal_status="need_more_steps", tasks=[Task(type="cancel")])

        prompt = await self._build_intent_prompt(
            plan,
            conversation,
            recent_ops,
            user_message,
            pending_follow_up=pending_follow_up,
        )
        self._raise_if_cancelled()
        return await self.planner.request_task_plan(
            create_completion=self._get_llm_client().chat.completions.create,
            model=settings.INTENT_MODEL_NAME or settings.MODEL_NAME,
            system_message=self._get_intent_system_message(),
            prompt=prompt,
        )

    def _get_intent_system_message(self) -> str:
        """Return the system message used for the intent-recognition planner call."""
        return "你是文档编辑器的意图识别模块。只输出 JSON，不要输出 Markdown 代码块，不要解释。"

    async def _build_intent_prompt(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> str:
        """Construct the prompt for the intent-recognition call."""
        prompt_context = await self._build_planner_prompt_context(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message=user_message,
        )
        follow_up_context = self._build_pending_follow_up_context(
            pending_follow_up,
            resume_target=self._get_pending_follow_up_resume_target(),
        )
        return self._render_intent_prompt(
            prompt_context=prompt_context,
            follow_up_context=follow_up_context,
            user_message=user_message,
        )

    async def _build_planner_prompt_context(
        self,
        *,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> PlannerPromptContext:
        """Assemble the shared planner prompt context from runtime state."""
        return PlannerPromptContext(
            tools_summary="\n".join(
                self._render_intent_tool_summary(item)
                for item in self._iter_intent_tools()
            ),
            ops_summary=self._summarize_operations(recent_ops),
            recent_tool_results=self._render_recent_tool_results(recent_ops),
            completion_criteria=self._build_completion_criteria_text(),
            context_snapshot=await self._compose_context_snapshot(
                plan=plan,
                conversation=conversation,
                recent_ops=recent_ops,
                user_message=user_message,
            ),
        )

    def _get_pending_follow_up_resume_target(self) -> str:
        """Describe the current document target when resuming after a follow-up."""
        return "当前教案"

    def _render_intent_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        follow_up_context: str,
        user_message: str,
    ) -> str:
        """Render the intent prompt from shared planner context."""
        return self.planner.build_intent_prompt(
            prompt_context=prompt_context,
            follow_up_context=follow_up_context,
            user_message=user_message,
        )

    async def _plan_next_round_after_execution(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        completed_steps: list[str],
    ) -> TaskList | None:
        """Ask the LLM whether the current request is finished or needs another planning round."""
        prompt = await self._build_replan_prompt(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message=user_message,
            completed_steps=completed_steps,
        )
        self._raise_if_cancelled()
        return await self.planner.request_task_plan(
            create_completion=self._get_llm_client().chat.completions.create,
            model=settings.INTENT_MODEL_NAME or settings.MODEL_NAME,
            system_message=self._get_replan_system_message(),
            prompt=prompt,
        )

    def _get_replan_system_message(self) -> str:
        """Return the system message used for the post-execution replanner call."""
        return "你是文档编辑器的继续规划模块。只输出 JSON，不要输出 Markdown 代码块，不要解释。"

    async def _build_replan_prompt(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        completed_steps: list[str],
    ) -> str:
        """Construct the prompt for the post-execution replanner call."""
        prompt_context = await self._build_planner_prompt_context(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message=user_message,
        )
        return self._render_replan_prompt(
            prompt_context=prompt_context,
            completed_steps=completed_steps,
            user_message=user_message,
        )

    def _render_replan_prompt(
        self,
        *,
        prompt_context: PlannerPromptContext,
        completed_steps: list[str],
        user_message: str,
    ) -> str:
        """Render the post-execution replanner prompt from shared planner context."""
        return self.planner.build_replan_prompt(
            prompt_context=prompt_context,
            completed_steps=completed_steps,
            user_message=user_message,
        )

    def _iter_intent_tools(self) -> list[Any]:
        """Return tools that should appear in planner prompts."""
        return [
            item
            for item in self.tools_registry.list_tools()
            if item.name != REQUEST_CONFIRMATION_TOOL_NAME
        ]

    def _build_guard_follow_up_planner_hint(self, pending_follow_up: dict[str, Any] | None) -> str:
        """Allow subclasses to inject extra resume rules for editor-generated follow-ups."""
        _ = pending_follow_up
        return ""

    def _build_pending_follow_up_context(
        self,
        pending_follow_up: dict[str, Any] | None,
        *,
        resume_target: str,
    ) -> str:
        """Render the persisted follow-up interruption into planner context text."""
        if not pending_follow_up:
            return ""

        question = str(pending_follow_up.get("question") or "").strip()
        options = pending_follow_up.get("options")
        options_text = json.dumps(options, ensure_ascii=False) if isinstance(options, list) else "[]"
        previous_user_message = str(pending_follow_up.get("previous_user_message") or "").strip()
        root_user_message = str(pending_follow_up.get("root_user_message") or previous_user_message).strip()
        completed_steps = pending_follow_up.get("completed_steps")
        completed_steps_text = self._render_completed_steps_text(completed_steps)
        remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
        remaining_tasks_text = self._summarize_task_queue(remaining_tasks)
        planner_hint = self._build_guard_follow_up_planner_hint(pending_follow_up)
        return (
            "当前用户正在回答上一轮追问。\n"
            f"- 上一轮追问：{question or '无'}\n"
            f"- 可选项：{options_text}\n"
            f"- 最初原始需求：{root_user_message or '无'}\n"
            f"- 上一轮原始需求：{previous_user_message or '无'}\n"
            f"- 追问前已完成：{completed_steps_text}\n"
            f"- 待继续任务：{remaining_tasks_text}\n"
            f"{planner_hint}"
            f"请把本轮消息视为对该追问的补充回答，再结合{resume_target}决定下一步任务。\n\n"
        )

    def _clean_text(self, value: Any) -> str:
        """Normalize free-form text extracted from the LLM or user message."""
        if not isinstance(value, str):
            return ""
        return value.strip().strip("：:，,。；;“”\"'` ")

    def _build_completion_criteria_text(self) -> str:
        """Describe what counts as task completion for the planner."""
        return self.planner.build_completion_criteria_text()

    def _summarize_result_for_prompt(self, result: dict[str, Any]) -> dict[str, Any]:
        """Keep recent tool results compact but structured for the next planning round."""
        summary: dict[str, Any] = {}
        for key in ("ok", "message", "error", "keyword", "query"):
            if key in result:
                summary[key] = self._json_ready(result.get(key))
        if isinstance(result.get("matches"), list):
            summary["matches_count"] = len(result["matches"])
            summary["matches_preview"] = self._json_ready(result["matches"][:3])
        if isinstance(result.get("section"), dict):
            summary["section"] = self._json_ready(result["section"])
        if isinstance(result.get("slide"), dict):
            summary["slide"] = self._json_ready(result["slide"])
        if isinstance(result.get("reasons"), list):
            summary["reasons"] = self._json_ready(result["reasons"][:3])
        if not summary:
            summary = self._json_ready(result) if isinstance(result, dict) else {"result": self._json_ready(result)}
        return summary

    def _render_recent_tool_results(self, recent_ops: list[Operation]) -> str:
        """Render recent tool executions as structured JSON-like records for prompting."""
        if not recent_ops:
            return "[]"

        items: list[dict[str, Any]] = []
        for operation in recent_ops[-4:]:
            arguments = operation.arguments if isinstance(operation.arguments, dict) else {}
            result = operation.result if isinstance(operation.result, dict) else {}
            items.append(
                {
                    "tool_name": operation.tool_name,
                    "arguments": self._json_ready(arguments),
                    "result": self._summarize_result_for_prompt(result),
                }
            )
        return json.dumps(items, ensure_ascii=False, indent=2)

    def _normalize_optional_int(self, value: Any) -> Any:
        """Coerce lightly dirty numeric inputs while preserving invalid raw values."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            match = re.search(r"-?\d+", raw)
            if match:
                try:
                    return int(match.group())
                except ValueError:
                    return value
        return value

    def _normalize_optional_bool(self, value: Any) -> Any:
        """Coerce common yes/no drift for boolean tool fields."""
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in {"true", "1", "yes", "y", "是", "需要", "要", "保留"}:
                return True
            if raw in {"false", "0", "no", "n", "否", "不需要", "不要", "不保留"}:
                return False
        return value

    def _normalize_optional_string(self, value: Any) -> str | None:
        """Coerce optional text fields while preserving explicit null semantics."""
        if value is None:
            return None
        return str(value).strip()

    def _normalize_replace_presentation_slide(self, slide: Any) -> Any:
        """Repair common LLM drift inside one slide payload before schema validation."""
        if not isinstance(slide, dict):
            return slide

        normalized = deepcopy(slide)
        if normalized.get("template") is None and normalized.get("layout") is not None:
            normalized["template"] = self._clean_text(normalized.get("layout"))

        for field_name in ("template", "layout", "title", "body"):
            value = normalized.get(field_name)
            normalized[field_name] = "" if value is None else str(value).strip()

        for field_name in ("subtitle", "image_description", "image_url", "notes", "source_section"):
            if field_name in normalized:
                normalized[field_name] = self._normalize_optional_string(normalized.get(field_name))

        if "game_index" in normalized:
            normalized["game_index"] = self._normalize_optional_int(normalized.get("game_index"))

        for field_name in ("link_text", "link_url"):
            if field_name in normalized:
                normalized[field_name] = self._normalize_optional_string(normalized.get(field_name))

        bullet_points = normalized.get("bullet_points")
        if isinstance(bullet_points, list):
            normalized["bullet_points"] = [
                str(item).strip()
                for item in bullet_points
                if item is not None and str(item).strip()
            ]
        return normalized

    def _get_initial_modify_execution_budget(
        self,
        tasks: list[Task],
        *,
        pending_follow_up: dict[str, Any] | None,
    ) -> int | None:
        """Decide whether the first modify task should pause for confirmation."""
        _ = tasks
        if pending_follow_up is not None:
            return None
        return 0

    def _should_pause_for_confirmation(
        self,
        task: Task,
        *,
        tool_name: str | None,
        arguments: dict[str, Any],
        remaining_tasks: list[Task],
        remaining_modify_budget: int | None,
    ) -> bool:
        """Allow subclasses to narrow confirmation to the truly risky modify tasks."""
        _ = tool_name
        _ = arguments
        _ = remaining_tasks
        return task.type == "modify" and remaining_modify_budget is not None and remaining_modify_budget <= 0

    async def _rewrite_task_queue(
        self,
        tasks: list[Task],
        *,
        quality_context: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Allow subclasses to repair or expand planned tasks before validation/execution."""
        _ = quality_context
        return tasks

    async def _process_task_queue(
        self,
        conversation_id: str,
        tasks: list[Task],
        *,
        modify_execution_budget: int | None,
        quality_context: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str], list[Task], dict[str, Any] | None, dict[str, Any] | None]:
        """Execute query tasks immediately and pause on the next unconfirmed modify task."""
        tool_events: list[str] = []
        response_texts: list[str] = []
        remaining_tasks = await self._rewrite_task_queue(
            [task.model_copy(deep=True) for task in tasks],
            quality_context=quality_context,
        )
        remaining_modify_budget = modify_execution_budget
        queue_follow_up = self._validate_task_queue(remaining_tasks, quality_context=quality_context)
        if queue_follow_up is not None:
            return tool_events, response_texts, remaining_tasks, queue_follow_up, None

        while remaining_tasks:
            self._raise_if_cancelled()
            task = remaining_tasks[0]

            if task.type == "confirm":
                remaining_tasks.pop(0)
                remaining_modify_budget = None
                continue

            if task.type == "cancel":
                remaining_tasks.clear()
                response_texts.append("已取消待办操作。")
                break

            if task.type == "reply":
                reply_text = str(task.response or task.proposed_content or "").strip()
                if reply_text:
                    response_texts.append(reply_text)
                remaining_tasks.pop(0)
                continue

            if task.type == "follow_up":
                remaining_tasks.pop(0)
                return tool_events, response_texts, remaining_tasks, self._build_follow_up_payload(task), None

            if self._should_pause_for_confirmation(
                task,
                tool_name=self._resolve_tool_name(task),
                arguments=self._build_task_arguments(task),
                remaining_tasks=remaining_tasks,
                remaining_modify_budget=remaining_modify_budget,
            ):
                tool_name = self._resolve_tool_name(task)
                arguments = self._build_task_arguments(task)
                content_quality_issues = self._validate_task_content_quality(task, quality_context)
                if content_quality_issues:
                    remaining_tasks.pop(0)
                    return (
                        tool_events,
                        response_texts,
                        remaining_tasks,
                        self._build_content_quality_follow_up(task, content_quality_issues),
                        None,
                    )
                validated_arguments, validation_issues = self._validate_tool_arguments(tool_name, arguments)
                if validation_issues:
                    remaining_tasks.pop(0)
                    return (
                        tool_events,
                        response_texts,
                        remaining_tasks,
                        self._build_invalid_task_follow_up(task, validation_issues),
                        None,
                    )
                task.parameters = deepcopy(validated_arguments)
                return (
                    tool_events,
                    response_texts,
                    remaining_tasks,
                    None,
                    self._build_confirmation_payload(remaining_tasks),
                )

            tool_name = self._resolve_tool_name(task)
            arguments = self._build_task_arguments(task)
            content_quality_issues = self._validate_task_content_quality(task, quality_context)
            if content_quality_issues:
                remaining_tasks.pop(0)
                return (
                    tool_events,
                    response_texts,
                    remaining_tasks,
                    self._build_content_quality_follow_up(task, content_quality_issues),
                    None,
                )
            validated_arguments, validation_issues = self._validate_tool_arguments(tool_name, arguments)
            if validation_issues:
                remaining_tasks.pop(0)
                return (
                    tool_events,
                    response_texts,
                    remaining_tasks,
                    self._build_invalid_task_follow_up(task, validation_issues),
                    None,
                )
            arguments = validated_arguments
            task.parameters = deepcopy(arguments)
            if tool_name:
                tool_events.append(
                    self._format_sse(
                        "tool",
                        {
                            "conversation_id": conversation_id,
                            "tool_name": tool_name,
                            "arguments": arguments,
                        },
                    )
                )

            result = await self._execute_task(conversation_id, task)
            self._raise_if_cancelled()
            described_result = self._describe_task_result(task, result)
            if tool_name:
                tool_events.append(
                    self._format_sse(
                        "tool_result",
                        {
                            "conversation_id": conversation_id,
                            "tool_name": tool_name,
                            "ok": self._task_succeeded(result),
                            "summary": described_result,
                            "result": self._json_ready(result),
                        },
                    )
                )
            if described_result:
                response_texts.append(described_result)
            remaining_tasks.pop(0)

            if task.type == "modify" and remaining_modify_budget is not None:
                remaining_modify_budget -= 1

            if not self._task_succeeded(result):
                if remaining_tasks:
                    response_texts.append("后续待办任务已停止，请根据当前结果重新发起需求。")
                remaining_tasks = []
                break

        return tool_events, response_texts, remaining_tasks, None, None

    def _validate_task_queue(
        self,
        tasks: list[Task],
        *,
        quality_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Allow subclasses to block unsafe multi-step task batches before execution."""
        return None

    async def _execute_task(self, conversation_id: str, task: Task) -> dict[str, Any]:
        """Resolve and execute a queued task."""
        tool_name = self._resolve_tool_name(task)
        if not tool_name:
            return {"ok": False, "message": "无法确定要执行的工具，请重新描述需求。"}
        return await self._execute_tool(conversation_id, tool_name, self._build_task_arguments(task))

    def _resolve_tool_name(self, task: Task) -> str | None:
        """Return the tool name explicitly selected by the agent."""
        return task.tool_name

    def _build_task_arguments(self, task: Task) -> dict[str, Any]:
        """Use the agent-produced tool arguments directly."""
        payload = deepcopy(task.parameters or {})
        return self._normalize_tool_arguments(self._resolve_tool_name(task), payload)

    def _normalize_tool_arguments(self, tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        """Normalize common LLM argument drift before previewing or executing tools."""
        payload = deepcopy(arguments)
        if tool_name == "insert_element":
            payload["target_section"] = self._clean_text(payload.get("target_section"))
            payload["position"] = self._normalize_position(payload.get("position"), insert_kind="element")
            payload["element_type"] = self._clean_text(payload.get("element_type")) or "补充内容"
            payload["content"] = self._clean_text(payload.get("content"))
        elif tool_name == "insert_section":
            payload["section_type"] = self._clean_text(payload.get("section_type"))
            payload["content"] = self._clean_text(payload.get("content"))
            payload["position"] = self._normalize_position(payload.get("position"), insert_kind="section")
            if payload.get("reference_section") is not None:
                payload["reference_section"] = self._clean_text(payload.get("reference_section"))
            if payload.get("reference_index") is not None:
                payload["reference_index"] = self._normalize_optional_int(payload.get("reference_index"))
        elif tool_name == "add_image_placeholder":
            payload["section_type"] = self._clean_text(payload.get("section_type"))
            payload["position"] = self._normalize_position(payload.get("position"), insert_kind="element")
            payload["description"] = self._clean_text(payload.get("description"))
            if payload.get("paragraph_index") is not None:
                payload["paragraph_index"] = self._normalize_optional_int(payload.get("paragraph_index"))
        elif tool_name in {"rewrite_section", "adjust_duration", "delete_section", "move_section"}:
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if tool_name in {"delete_section", "move_section"} and payload.get("section_index") is not None:
                payload["section_index"] = self._normalize_optional_int(payload.get("section_index"))
            if tool_name == "adjust_duration":
                payload["new_duration"] = self._normalize_optional_int(payload.get("new_duration"))
            if tool_name == "move_section":
                payload["new_index"] = self._normalize_optional_int(payload.get("new_index"))
        elif tool_name == "search_in_plan":
            payload["keyword"] = self._clean_text(payload.get("keyword"))
        elif tool_name == "get_section_details":
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("section_index") is not None:
                payload["section_index"] = self._normalize_optional_int(payload.get("section_index"))
            if payload.get("include_neighbors") is not None:
                payload["include_neighbors"] = self._normalize_optional_bool(payload.get("include_neighbors"))
        elif tool_name == "get_text_context_in_plan":
            payload["target_text"] = self._clean_text(payload.get("target_text"))
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("max_matches") is not None:
                payload["max_matches"] = self._normalize_optional_int(payload.get("max_matches"))
        elif tool_name == "replace_text_in_plan":
            payload["target_text"] = self._clean_text(payload.get("target_text"))
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("replace_all") is not None:
                payload["replace_all"] = self._normalize_optional_bool(payload.get("replace_all"))
        elif tool_name == "replace_paragraphs_in_section":
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("section_index") is not None:
                payload["section_index"] = self._normalize_optional_int(payload.get("section_index"))
            payload["start_paragraph_index"] = self._normalize_optional_int(payload.get("start_paragraph_index"))
            if payload.get("end_paragraph_index") is not None:
                payload["end_paragraph_index"] = self._normalize_optional_int(payload.get("end_paragraph_index"))
        elif tool_name == "insert_paragraphs_in_section":
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("section_index") is not None:
                payload["section_index"] = self._normalize_optional_int(payload.get("section_index"))
            payload["position"] = self._normalize_position(payload.get("position"), insert_kind="section")
            if payload.get("paragraph_index") is not None:
                payload["paragraph_index"] = self._normalize_optional_int(payload.get("paragraph_index"))
        elif tool_name == "delete_paragraphs_in_section":
            if payload.get("section_type") is not None:
                payload["section_type"] = self._clean_text(payload.get("section_type"))
            if payload.get("section_index") is not None:
                payload["section_index"] = self._normalize_optional_int(payload.get("section_index"))
            payload["start_paragraph_index"] = self._normalize_optional_int(payload.get("start_paragraph_index"))
            if payload.get("end_paragraph_index") is not None:
                payload["end_paragraph_index"] = self._normalize_optional_int(payload.get("end_paragraph_index"))
        elif tool_name == "evaluate_plan_suitability":
            payload["focus"] = self._clean_text(payload.get("focus"))
        elif tool_name == "add_slide":
            payload["template"] = self._clean_text(payload.get("template")) or payload.get("template")
            payload["title"] = self._clean_text(payload.get("title"))
            if payload.get("subtitle") is not None:
                payload["subtitle"] = self._clean_text(payload.get("subtitle"))
            if payload.get("body") is not None:
                payload["body"] = str(payload.get("body") or "").strip()
            if payload.get("image_description") is not None:
                payload["image_description"] = str(payload.get("image_description") or "").strip()
            if payload.get("after_slide_index") is not None:
                payload["after_slide_index"] = self._normalize_optional_int(payload.get("after_slide_index"))
            if payload.get("before_slide_index") is not None:
                payload["before_slide_index"] = self._normalize_optional_int(payload.get("before_slide_index"))
        elif tool_name in {
            "update_slide_content",
            "set_bullet_points",
            "change_layout",
            "add_notes",
            "duplicate_slide",
            "delete_slide",
            "move_slide",
        }:
            if payload.get("slide_index") is not None:
                payload["slide_index"] = self._normalize_optional_int(payload.get("slide_index"))
            if payload.get("new_index") is not None:
                payload["new_index"] = self._normalize_optional_int(payload.get("new_index"))
            if payload.get("title") is not None:
                payload["title"] = self._clean_text(payload.get("title"))
            if payload.get("subtitle") is not None:
                payload["subtitle"] = self._clean_text(payload.get("subtitle"))
            if payload.get("template") is not None:
                payload["template"] = self._clean_text(payload.get("template"))
            if payload.get("new_layout") is not None:
                payload["new_layout"] = self._clean_text(payload.get("new_layout"))
            if payload.get("game_index") is not None:
                payload["game_index"] = self._normalize_optional_int(payload.get("game_index"))
            for field_name in ("body", "image_description", "image_url", "notes", "source_section"):
                if payload.get(field_name) is not None:
                    payload[field_name] = str(payload.get(field_name) or "").strip()
        elif tool_name == "replace_presentation":
            if payload.get("title") is not None:
                payload["title"] = self._clean_text(payload.get("title"))
            if payload.get("classroom_script") is not None:
                payload["classroom_script"] = str(payload.get("classroom_script") or "").strip()
            slides = payload.get("slides")
            if isinstance(slides, list):
                payload["slides"] = [self._normalize_replace_presentation_slide(slide) for slide in slides]
        elif tool_name == "get_presentation_outline":
            if payload.get("max_slides") is not None:
                payload["max_slides"] = self._normalize_optional_int(payload.get("max_slides"))
            if payload.get("include_classroom_script") is not None:
                payload["include_classroom_script"] = self._normalize_optional_bool(
                    payload.get("include_classroom_script")
                )
        elif tool_name == "get_slide_details":
            if payload.get("slide_index") is not None:
                payload["slide_index"] = self._normalize_optional_int(payload.get("slide_index"))
            if payload.get("title_keyword") is not None:
                payload["title_keyword"] = self._clean_text(payload.get("title_keyword"))
            if payload.get("include_neighbors") is not None:
                payload["include_neighbors"] = self._normalize_optional_bool(payload.get("include_neighbors"))
        elif tool_name == "search_in_presentation":
            payload["keyword"] = self._clean_text(payload.get("keyword"))
            if payload.get("max_matches") is not None:
                payload["max_matches"] = self._normalize_optional_int(payload.get("max_matches"))
        elif tool_name == "search_web":
            payload["query"] = self._clean_text(payload.get("query"))
            if payload.get("top_k") is not None:
                payload["top_k"] = self._normalize_optional_int(payload.get("top_k"))
        return payload

    def _normalize_position(self, value: Any, *, insert_kind: str) -> str:
        """Map natural-language position labels to the tool's literal enum."""
        raw = self._clean_text(value)
        if not raw:
            return "end"
        aliases = SECTION_INSERT_POSITION_ALIASES if insert_kind == "section" else ELEMENT_INSERT_POSITION_ALIASES
        return aliases.get(raw, raw)

    def _build_follow_up_payload(self, task: Task) -> dict[str, Any]:
        """Build a structured follow-up payload from a task."""
        return self.planner.build_follow_up_payload(task)

    def _decorate_follow_up_payload(
        self,
        follow_up: dict[str, Any],
        *,
        previous_user_message: str,
        root_user_message: str | None = None,
        completed_steps: list[str],
        remaining_tasks: list[Task],
    ) -> dict[str, Any]:
        """Attach resumable task context to a follow-up payload."""
        return self.planner.decorate_follow_up_payload(
            follow_up,
            previous_user_message=previous_user_message,
            root_user_message=root_user_message,
            completed_steps=completed_steps,
            remaining_tasks=remaining_tasks,
        )

    def _build_confirmation_payload(self, task: Task | list[Task]) -> dict[str, Any]:
        """Build the existing confirmation SSE payload for the next modify task."""
        task_list = task if isinstance(task, list) else [task]
        modify_tasks = [item for item in task_list if item.type == "modify"]
        primary_task = modify_tasks[0] if modify_tasks else task_list[0]
        tool_name = self._resolve_tool_name(primary_task) or ""
        arguments = self._build_task_arguments(primary_task)
        if len(modify_tasks) <= 1:
            proposed_changes = primary_task.proposed_content or self._render_arguments_preview(arguments)
            operation_description = self._build_generic_operation_description(primary_task, tool_name, arguments)
        else:
            proposed_changes = self._render_confirmation_batch_preview(modify_tasks)
            operation_description = f"准备连续执行 {len(modify_tasks)} 项修改"
        return {
            "type": "confirmation_required",
            "operation_description": operation_description,
            "proposed_changes": proposed_changes,
            "tool_to_confirm": tool_name,
            "tool_args": self._json_ready(arguments),
        }

    def _render_confirmation_batch_preview(self, tasks: list[Task]) -> str:
        """Render a grouped confirmation preview for multiple queued modify tasks."""
        lines: list[str] = []
        for index, task in enumerate(tasks[:5], start=1):
            tool_name = self._resolve_tool_name(task) or ""
            arguments = self._build_task_arguments(task)
            description = self._build_generic_operation_description(task, tool_name, arguments)
            preview = str(task.proposed_content or self._render_arguments_preview(arguments)).strip()
            lines.append(f"{index}. {description}")
            if preview:
                lines.append(self._truncate_text(preview, 240))
        if len(tasks) > 5:
            lines.append(f"其余 {len(tasks) - 5} 项修改会在确认后继续执行。")
        return "\n".join(lines)

    def _describe_task_result(self, task: Task, result: dict[str, Any]) -> str:
        """Render a concise user-facing summary for a task result."""
        error_message = str(result.get("error") or "").strip()
        if error_message:
            return error_message

        message = str(result.get("message") or "").strip()
        if message:
            if task.type == "query" and isinstance(result.get("section"), dict):
                section = result["section"]
                section_name = str(section.get("section_type") or "目标章节").strip()
                lines = [message, f"- 章节: {section_name}"]
                duration = section.get("duration")
                if duration is not None:
                    lines.append(f"- 时长: {duration} 分钟")
                paragraphs = section.get("paragraphs")
                if isinstance(paragraphs, list) and paragraphs:
                    for item in paragraphs[:5]:
                        if not isinstance(item, dict):
                            continue
                        index = item.get("index")
                        text = str(item.get("text") or "").strip()
                        if text:
                            lines.append(f"- 第{int(index) + 1 if isinstance(index, int) else '?'}段: {text}")
                return "\n".join(lines)
            if task.type == "query" and result.get("matches"):
                if any(
                    isinstance(item, dict) and ("slide_index" in item or "field" in item)
                    for item in result.get("matches", [])
                ):
                    return message
                lines = [message]
                for item in result.get("matches", [])[:5]:
                    if not isinstance(item, dict):
                        continue
                    section = item.get("section") or item.get("section_type") or "未知位置"
                    snippet = item.get("snippet") or ""
                    path = str(item.get("path") or "").strip()
                    if path:
                        lines.append(f"- {section} [{path}]: {snippet}")
                    else:
                        lines.append(f"- {section}: {snippet}")
                return "\n".join(lines)
            if task.type == "query" and result.get("reasons"):
                lines = [message]
                for item in result.get("reasons", [])[:5]:
                    lines.append(f"- {item}")
                return "\n".join(lines)
            return message
        if task.type == "query":
            return "已完成查询，但工具没有返回可展示的文本结果。"
        if task.type == "modify":
            return "已执行修改，但工具没有返回详细说明。"
        return json.dumps(self._json_ready(result), ensure_ascii=False)

    def _task_succeeded(self, result: dict[str, Any]) -> bool:
        """Check whether a task completed successfully."""
        return bool(result.get("ok", True))

    def _build_final_reply(self, response_texts: list[str]) -> str:
        """Create the compact final reply used by the done event."""
        filtered = [text.strip() for text in response_texts if text and text.strip()]
        if filtered:
            return "\n\n".join(filtered)
        return "本轮处理已完成，但工具没有返回可展示的文本结果。"

    def _build_follow_up_payload_from_task_plan(
        self,
        task_plan: TaskList,
        *,
        fallback_question: str,
    ) -> dict[str, Any]:
        """Prefer the planner-authored follow-up task, but keep a safe fallback."""
        return self.planner.build_follow_up_payload_from_task_plan(
            task_plan,
            fallback_question=fallback_question,
        )

    def _parse_task_plan_from_llm_output(self, content: str) -> TaskList | None:
        """Parse the LLM JSON output into a validated task plan with explicit status."""
        return self.planner.parse_task_plan_from_llm_output(content)

    def _parse_tasks_from_llm_output(self, content: str) -> list[Task]:
        """Backward-compatible wrapper for older callers and tests."""
        task_plan = self._parse_task_plan_from_llm_output(content)
        return task_plan.tasks if task_plan is not None else []

    def _extract_json_payload(self, content: str) -> str:
        """Extract a JSON object or array from a raw LLM response."""
        return self.planner.extract_json_payload(content)

    def _render_arguments_preview(self, arguments: dict[str, Any]) -> str:
        """Create a readable preview for confirmation messages."""
        if not arguments:
            return "将执行该修改。"
        return json.dumps(self._json_ready(arguments), ensure_ascii=False, indent=2)

    def _build_generic_operation_description(self, task: Task, tool_name: str, arguments: dict[str, Any]) -> str:
        """Create a generic confirmation title without per-tool hardcoding."""
        subject = self._infer_task_subject(arguments, self._clean_text(task.target))
        action = self._clean_text(task.action) or tool_name or "修改"
        return f"准备{action}“{subject}”"

    def _infer_task_subject(self, arguments: dict[str, Any], fallback: str = "") -> str:
        """Pick a readable subject from common target-like tool arguments."""
        for key in ("target_section", "section_type", "reference_section", "keyword", "target_text", "focus", "target"):
            value = self._clean_text(arguments.get(key))
            if value:
                return value
        title = self._clean_text(arguments.get("title"))
        if title:
            return title
        subtitle = self._clean_text(arguments.get("subtitle"))
        if subtitle:
            return subtitle
        slide_index = arguments.get("slide_index")
        if isinstance(slide_index, int) and slide_index >= 0:
            return f"第{slide_index + 1}页"
        title_keyword = self._clean_text(arguments.get("title_keyword"))
        if title_keyword:
            return title_keyword
        query = self._clean_text(arguments.get("query"))
        if query:
            return query
        return fallback or "当前内容"

    def _render_intent_tool_summary(self, tool: Any) -> str:
        """Render a compact tool + schema summary for the intent prompt."""
        parts = [f"- {tool.name}: {tool.description}"]
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return "".join(parts)

        try:
            schema = args_schema.model_json_schema()
        except Exception:  # noqa: BLE001
            return "".join(parts)

        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))
        rendered_fields: list[str] = []
        for field_name, meta in properties.items():
            if not isinstance(meta, dict):
                continue
            field_parts = [field_name]
            if field_name in required_fields:
                field_parts.append("必填")
            enum_values = meta.get("enum")
            field_type = meta.get("type")
            if isinstance(enum_values, list) and enum_values:
                field_parts.append("枚举=" + "/".join(str(item) for item in enum_values))
            elif isinstance(field_type, str) and field_type:
                field_parts.append(field_type)
            description = self._clean_text(meta.get("description"))
            if description:
                field_parts.append(description)
            rendered_fields.append("（" + "，".join(field_parts) + "）")

        if rendered_fields:
            parts.append(" 参数: " + " ".join(rendered_fields))
        return "".join(parts)

    def _validate_tool_arguments(
        self,
        tool_name: str | None,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Validate normalized arguments against the selected tool schema."""
        return self.guardrails.validate_tool_arguments(tool_name, arguments)

    def _format_tool_validation_issues(self, exc: Any) -> list[str]:
        """Convert pydantic validation errors into short readable issues."""
        return self.guardrails.format_tool_validation_issues(exc)

    def _humanize_validation_location(self, location: str) -> str:
        """Translate raw validation paths into user-facing labels."""
        return self.guardrails.humanize_validation_location(location)

    def _humanize_validation_message(self, message: str) -> str:
        """Rewrite generic validator output into more actionable Chinese hints."""
        return self.guardrails.humanize_validation_message(message)

    def _humanize_validation_issue_text(self, issue: str) -> str:
        """Humanize preformatted validation issue strings as a final fallback."""
        return self.guardrails.humanize_validation_issue_text(issue)

    def _describe_invalid_task_action(self, task: Task) -> str:
        """Choose a human-friendly action label for follow-up questions."""
        return self.guardrails.describe_invalid_task_action(task, resolve_tool_name=self._resolve_tool_name)

    def _build_invalid_task_follow_up(self, task: Task, issues: list[str]) -> dict[str, Any]:
        """Ask the user for missing/invalid tool inputs instead of guessing."""
        return self.guardrails.build_invalid_task_follow_up(
            task,
            issues,
            resolve_tool_name=self._resolve_tool_name,
        )

    def _build_intent_failure_follow_up(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask a grounded clarification question when intent recognition stays ambiguous."""
        focus_indices = self._select_focus_section_indices(plan, conversation, recent_ops, user_message)
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        focus_labels: list[str] = []
        if isinstance(sections, list):
            for index in focus_indices[:2]:
                if not (0 <= index < len(sections)):
                    continue
                section = sections[index]
                if not isinstance(section, dict):
                    continue
                focus_labels.append(self._get_section_label(section, fallback=f"章节{index + 1}"))
        return self.planner.build_ambiguous_intent_follow_up(
            focus_labels=focus_labels,
            focus_kind="章节",
            pending_follow_up=pending_follow_up,
            pending_question_template=(
                "我还没能把你的补充信息稳定映射成具体修改。"
                "{focus_sentence}"
                "请直接告诉我你要改哪一部分，以及希望改成的最终内容或效果。"
            ),
            initial_question_template=(
                "我先基于当前教案做了判断，但你的意图还不够明确。"
                "{focus_sentence}"
                "你是想修改这一部分吗？如果是，请直接补充要怎么改；如果不是，也请直接说明目标章节和改动要求。"
            ),
        )

    def _build_content_quality_context(
        self,
        user_message: str,
        *,
        pending_follow_up: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Infer whether the request expects final-form content such as runnable code."""
        return self.guardrails.build_content_quality_context(
            user_message,
            pending_follow_up=pending_follow_up,
        )

    def _validate_task_content_quality(
        self,
        task: Task,
        quality_context: dict[str, Any] | None,
    ) -> list[str] | None:
        """Block placeholder-like draft text when the user asked for final content."""
        return self.guardrails.validate_task_content_quality(
            task,
            quality_context,
            build_task_arguments=self._build_task_arguments,
        )

    def _build_content_quality_follow_up(self, task: Task, issues: list[str]) -> dict[str, Any]:
        """Ask for tighter constraints when the generated content still looks like a draft."""
        return self.guardrails.build_content_quality_follow_up(
            task,
            issues,
            build_task_arguments=self._build_task_arguments,
            infer_task_subject=self._infer_task_subject,
        )

    async def _run_db(self, operation: Callable[[PlanService, ConversationService, OperationService], Any]) -> Any:
        """Execute database work against the current request-scoped session."""
        self._raise_if_cancelled()
        return operation(self.plan_service, self.conv_service, self.op_service)

    def _raise_if_cancelled(self) -> None:
        """Abort this run when the client disconnected or a newer run superseded it."""
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

    def _get_root_user_message(
        self,
        user_message: str,
        pending_follow_up: dict[str, Any] | None,
    ) -> str:
        """Keep the original user goal across chained follow-up questions."""
        if pending_follow_up is None:
            return user_message.strip()
        root = str(pending_follow_up.get("root_user_message") or "").strip()
        if root:
            return root
        previous = str(pending_follow_up.get("previous_user_message") or "").strip()
        return previous or user_message.strip()

    def _start_disconnect_watcher(
        self,
        disconnect_checker: Callable[[], Any] | None,
        cancel_token: CancellationToken,
    ) -> asyncio.Task[None] | None:
        """Poll the ASGI request state so server-side work stops after disconnects."""
        if disconnect_checker is None:
            return None
        return asyncio.create_task(self._watch_for_disconnect(disconnect_checker, cancel_token))

    async def _watch_for_disconnect(
        self,
        disconnect_checker: Callable[[], Any],
        cancel_token: CancellationToken,
    ) -> None:
        """Cancel the active run once the client connection is gone."""
        try:
            while not cancel_token.is_cancelled():
                disconnected = disconnect_checker()
                if asyncio.iscoroutine(disconnected):
                    disconnected = await disconnected
                if disconnected:
                    cancel_token.cancel()
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _build_system_prompt(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> str:
        """Generate the system prompt with plan context and tool descriptions."""
        tools_summary = "\n".join(
            f"- {item.name}: {item.description}" for item in self.tools_registry.list_tools()
        )
        global_preferences = self._get_active_preferences_text(self.user_id)
        temp_preferences = self.conv_service.get_temp_preferences(conversation.id)
        temp_preferences_text = render_temp_preferences_text(temp_preferences)
        context_snapshot = await self._compose_context_snapshot(
            plan=plan,
            conversation=conversation,
            recent_ops=recent_ops,
            user_message=user_message,
        )
        global_preferences_text = global_preferences or "暂无激活的全局偏好。"

        return (
            "你是教案智能体的文档编辑器。\n"
            "你可以基于当前教案内容回答问题，必要时调用工具完成修改。\n"
            "当用户目标缺少关键条件、无法可靠执行时，优先调用 ask_follow_up 提出一个明确的澄清问题，不要擅自猜测。\n"
            "当操作可能删除、覆盖或显著改写内容时，优先调用 request_confirmation 请求用户确认，再执行目标工具。\n"
            "如果工具返回错误，请解释原因并给出下一步建议。\n\n"
            f"当前上下文：\n{context_snapshot}\n\n"
            f"全局偏好注入：\n{global_preferences_text}\n\n"
            f"临时偏好：{temp_preferences_text}\n\n"
            f"可用工具：\n{tools_summary}"
        )

    def _get_missing_plan_message(self) -> str:
        """Return the not-found error message for the current document type."""
        return "未找到对应教案。"

    def _summarize_operations(self, recent_ops: list[Operation]) -> str:
        """Render operation history into a compact prompt summary."""
        if not recent_ops:
            return "暂无历史操作。"

        lines = []
        for item in recent_ops[-self.history_limit :]:
            arguments = item.arguments if isinstance(item.arguments, dict) else {}
            result = item.result if isinstance(item.result, dict) else {}
            focus = (
                arguments.get("section_type")
                or arguments.get("target_section")
                or arguments.get("title")
                or arguments.get("subtitle")
                or arguments.get("keyword")
                or arguments.get("query")
                or arguments.get("title_keyword")
                or arguments.get("target_text")
                or arguments.get("focus")
                or arguments.get("target")
            )
            if not focus and isinstance(arguments.get("slide_index"), int):
                focus = f"第{int(arguments['slide_index']) + 1}页"
            message = str(result.get("message") or result.get("error") or "").strip()
            prefix = f"- {item.tool_name}"
            if focus:
                prefix = f"{prefix} [{focus}]"
            if message:
                lines.append(f"{prefix}: {self._truncate_text(message, 160)}")
            else:
                serialized = json.dumps(self._json_ready(result), ensure_ascii=False)
                lines.append(f"{prefix}: {self._truncate_text(serialized, 160)}")
        return "\n".join(lines)

    async def _compose_context_snapshot(
        self,
        *,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> str:
        """Build a layered context snapshot instead of injecting the whole plan JSON."""
        focus_indices = self._select_focus_section_indices(plan, conversation, recent_ops, user_message)
        blocks = [
            ("文档概要", self._build_plan_outline(plan)),
            ("用户指向内容", self._build_targeted_context_text(plan, user_message)),
            ("焦点章节", self._build_focus_sections_text(plan, focus_indices)),
            ("会话记忆", self._build_session_memory_text(conversation)),
            ("最近操作", self._summarize_operations(recent_ops)),
        ]
        rendered = []
        for title, body in blocks:
            if body and body.strip():
                rendered.append(f"[{title}]\n{body.strip()}")
        return "\n\n".join(rendered)

    def _build_plan_outline(self, plan: Plan) -> str:
        """Summarize plan metadata and section outline."""
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        if not isinstance(sections, list):
            sections = []

        lines = [
            f"标题：{plan.title}",
            f"类型：{plan.doc_type}",
            f"学科：{plan.subject or '未提供'}",
            f"年级：{plan.grade or '未提供'}",
            f"章节数：{len(sections)}",
        ]
        for index, section in enumerate(sections[:12], start=1):
            if not isinstance(section, dict):
                continue
            label = self._get_section_label(section, fallback=f"章节{index}")
            duration = section.get("duration")
            preview = self._summarize_section_preview(section)
            if duration is None:
                lines.append(f"- {index}. {label} | {preview}")
            else:
                lines.append(f"- {index}. {label}（{duration}分钟）| {preview}")
        if len(sections) > 12:
            lines.append(f"- 其余 {len(sections) - 12} 个章节已省略")
        return "\n".join(lines)

    def _build_focus_sections_text(self, plan: Plan, focus_indices: list[int]) -> str:
        """Render only the most relevant sections for the current round."""
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        if not isinstance(sections, list) or not sections:
            return "当前教案没有可用章节。"

        rendered: list[str] = []
        for index in focus_indices[:MAX_CONTEXT_SECTIONS]:
            if not (0 <= index < len(sections)):
                continue
            section = sections[index]
            if not isinstance(section, dict):
                continue
            label = self._get_section_label(section, fallback=f"章节{index + 1}")
            serialized = json.dumps(section, ensure_ascii=False, indent=2)
            rendered.append(f"{index + 1}. {label}\n{self._truncate_text(serialized, MAX_SECTION_CONTEXT_CHARS)}")
        return "\n\n".join(rendered)

    def _build_session_memory_text(self, conversation: Conversation) -> str:
        """Render recent turn memory plus any persisted summary."""
        metadata = conversation.metadata_json or {}
        lines: list[str] = []
        summary = str(conversation.summary or "").strip()
        if summary:
            lines.append(f"会话摘要：{summary}")

        recent_turns = self._get_recent_turns(conversation)
        if recent_turns:
            lines.append("最近轮次：")
            for item in recent_turns[-RECENT_TURNS_LIMIT:]:
                role = "用户" if item.get("role") == "user" else "助手"
                kind = str(item.get("kind") or "message")
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                if kind in {"follow_up", "confirmation"}:
                    lines.append(f"- {role}/{kind}: {content}")
                else:
                    lines.append(f"- {role}: {content}")

        pending_tasks = metadata.get("pending_tasks")
        if isinstance(pending_tasks, list) and pending_tasks:
            lines.append(f"待确认任务数：{len(pending_tasks)}")
        pending_follow_up = self._get_pending_follow_up(conversation)
        if pending_follow_up:
            question = str(pending_follow_up.get("question") or "").strip()
            if question:
                lines.append(f"当前待补充：{question}")
            remaining_tasks = self._load_tasks_from_payload(pending_follow_up.get("remaining_tasks"))
            if remaining_tasks:
                lines.append(f"待继续任务：{self._summarize_task_queue(remaining_tasks)}")
        return "\n".join(lines) if lines else "暂无会话记忆。"

    def _select_focus_section_indices(
        self,
        plan: Plan,
        conversation: Conversation,
        recent_ops: list[Operation],
        user_message: str,
    ) -> list[int]:
        """Pick the most relevant sections for this round."""
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        if not isinstance(sections, list) or not sections:
            return []

        explicit_section_indices = set(self._find_explicit_section_indices(sections, user_message))
        targeted_hit_indices = {
            item["section_index"]
            for item in self._find_targeted_text_hits(plan, user_message)
            if isinstance(item.get("section_index"), int)
        }

        combined_text = " ".join(
            part
            for part in [
                user_message,
                " ".join(str(item.get("content") or "") for item in self._get_recent_turns(conversation)[-3:]),
                " ".join(
                    str(operation.arguments.get("section_type") or operation.arguments.get("target_section") or "")
                    for operation in recent_ops[-3:]
                    if isinstance(operation.arguments, dict)
                ),
            ]
            if part
        ).lower()
        terms = self._extract_candidate_terms(combined_text)
        scored: list[tuple[int, int]] = []

        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            label = self._get_section_label(section, fallback=f"章节{index + 1}").lower()
            serialized = json.dumps(section, ensure_ascii=False).lower()
            score = 0
            if index in explicit_section_indices:
                score += 20
            if index in targeted_hit_indices:
                score += 24
            if label and label in combined_text:
                score += 8
            for term in terms:
                if term in label or (label and label in term):
                    score += 4
                elif term in serialized:
                    score += 1
            scored.append((score, index))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = [index for score, index in scored if score > 0][:2]
        if not selected:
            selected = [0]
            if len(sections) > 1:
                selected.append(1)

        expanded: list[int] = []
        for index in selected:
            for candidate in (index - 1, index, index + 1):
                if 0 <= candidate < len(sections) and candidate not in expanded:
                    expanded.append(candidate)
                if len(expanded) >= MAX_CONTEXT_SECTIONS:
                    break
            if len(expanded) >= MAX_CONTEXT_SECTIONS:
                break
        return expanded

    def _extract_candidate_terms(self, text: str) -> list[str]:
        """Extract lightweight match terms from the current user request."""
        terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text)
        ordered: list[str] = []
        for item in terms:
            if item not in ordered:
                ordered.append(item)
        return ordered[:12]

    def _extract_quoted_fragments(self, text: str) -> list[str]:
        """Extract user-quoted phrases that often point to exact content in the plan."""
        ordered: list[str] = []
        for raw in QUOTE_PATTERN.findall(text or ""):
            fragment = raw.strip()
            if len(fragment) < 2:
                continue
            if fragment not in ordered:
                ordered.append(fragment)
        return ordered[:6]

    def _collect_string_leaves(self, value: Any, path: str = "") -> list[tuple[str, str]]:
        """Collect string leaves from nested plan content for exact-text targeting."""
        if isinstance(value, str):
            return [(path or "root", value)]
        if isinstance(value, dict):
            leaves: list[tuple[str, str]] = []
            for key, item in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                leaves.extend(self._collect_string_leaves(item, next_path))
            return leaves
        if isinstance(value, list):
            leaves: list[tuple[str, str]] = []
            for index, item in enumerate(value):
                next_path = f"{path}[{index}]" if path else f"[{index}]"
                leaves.extend(self._collect_string_leaves(item, next_path))
            return leaves
        return []

    def _make_search_snippet(self, text: str, needle: str, radius: int = 60) -> str:
        """Render a short snippet around a matched user-targeted phrase."""
        lower_text = text.lower()
        lower_needle = needle.lower()
        position = lower_text.find(lower_needle)
        if position < 0:
            return self._truncate_text(text.replace("\n", " "), radius * 2)
        start = max(position - radius, 0)
        end = min(position + len(needle) + radius, len(text))
        snippet = text[start:end].replace("\n", " ")
        return self._truncate_text(snippet, radius * 2 + len(needle))

    def _find_explicit_section_indices(self, sections: list[Any], user_message: str) -> list[int]:
        """Find sections explicitly named by the user."""
        message = (user_message or "").strip().lower()
        if not message:
            return []

        indices: list[int] = []
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            label = self._get_section_label(section, fallback=f"章节{index + 1}").strip().lower()
            if label and label in message and index not in indices:
                indices.append(index)
        return indices

    def _find_targeted_text_hits(self, plan: Plan, user_message: str) -> list[dict[str, Any]]:
        """Find exact text snippets the user appears to be pointing at."""
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        if not isinstance(sections, list) or not sections:
            return []

        quoted_fragments = self._extract_quoted_fragments(user_message)
        if not quoted_fragments:
            return []

        hits: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            label = self._get_section_label(section, fallback=f"章节{index + 1}")
            for path, text in self._collect_string_leaves(section):
                lower_text = text.lower()
                for fragment in quoted_fragments:
                    if fragment.lower() not in lower_text:
                        continue
                    key = (index, path, fragment)
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(
                        {
                            "section_index": index,
                            "section": label,
                            "path": path,
                            "needle": fragment,
                            "snippet": self._make_search_snippet(text, fragment),
                        }
                    )
                    if len(hits) >= 6:
                        return hits
        return hits

    def _build_targeted_context_text(self, plan: Plan, user_message: str) -> str:
        """Surface the exact section/text targets mentioned by the user."""
        content = plan.content if isinstance(plan.content, dict) else {}
        sections = content.get("sections")
        if not isinstance(sections, list) or not sections:
            return ""

        lines: list[str] = []
        explicit_section_indices = self._find_explicit_section_indices(sections, user_message)
        if explicit_section_indices:
            labels = [
                self._get_section_label(sections[index], fallback=f"章节{index + 1}")
                for index in explicit_section_indices[:3]
                if 0 <= index < len(sections) and isinstance(sections[index], dict)
            ]
            if labels:
                lines.append(f"用户明确点名章节：{'、'.join(labels)}")

        text_hits = self._find_targeted_text_hits(plan, user_message)
        if text_hits:
            lines.append("用户消息中引用/指向的原文命中：")
            for item in text_hits[:4]:
                lines.append(
                    f"- {item['section']} | {item['path']} | 命中“{item['needle']}”：{item['snippet']}"
                )
        else:
            quoted_fragments = self._extract_quoted_fragments(user_message)
            if quoted_fragments:
                lines.append("用户引用了具体原句，但当前上下文里没有精确命中：")
                for fragment in quoted_fragments[:3]:
                    lines.append(f"- {fragment}")

        return "\n".join(lines)

    def _get_section_label(self, section: dict[str, Any], fallback: str) -> str:
        """Read a readable section label from one lesson section payload."""
        return str(
            section.get("type")
            or section.get("section_type")
            or section.get("title")
            or section.get("name")
            or fallback
        )

    def _summarize_section_preview(self, section: dict[str, Any]) -> str:
        """Build a short one-line preview for section outlines."""
        content = section.get("content")
        if isinstance(content, str) and content.strip():
            return self._truncate_text(content.strip().replace("\n", " "), 60)
        if isinstance(content, (list, dict)):
            return self._truncate_text(json.dumps(content, ensure_ascii=False), 60)
        elements = section.get("elements")
        if isinstance(elements, list) and elements:
            return self._truncate_text(json.dumps(elements[0], ensure_ascii=False), 60)
        return "无摘要"

    def _get_recent_turns(self, conversation: Conversation) -> list[dict[str, Any]]:
        """Load recent remembered turns from conversation metadata."""
        return self.state_store.get_recent_turns(conversation)

    async def _append_recent_turn(self, conversation_id: str, role: str, content: str, *, kind: str = "message") -> None:
        """Persist a short turn memory for future prompt assembly."""
        await self.state_store.append_recent_turn(conversation_id, role, content, kind=kind)

    def _truncate_text(self, text: str, limit: int) -> str:
        """Trim long prompt fragments while keeping them readable."""
        stripped = text.strip()
        if len(stripped) <= limit:
            return stripped
        return f"{stripped[:limit].rstrip()}..."

    def _normalize_tool_result(self, result: Any) -> dict[str, Any]:
        """Ensure tool results are JSON serializable and consistently shaped."""
        if isinstance(result, dict):
            payload = dict(result)
            if payload.get("type") in {"follow_up", "confirmation_required"}:
                return self._json_ready(payload)
            payload.setdefault("ok", True)
            return self._json_ready(payload)
        return {"ok": True, "result": self._json_ready(result)}

    def _get_active_preferences_text(self, user_id: str) -> str:
        """Collect active global preference prompt injections from the current session."""
        service = PreferenceService(self.db, user_id=user_id)
        presets = service.get_presets(user_id, active_only=True)
        injections = [item.prompt_injection.strip() for item in presets if item.prompt_injection.strip()]
        return "\n".join(injections)

    def _get_pending_tasks(self, conversation: Conversation) -> list[Task]:
        """Load queued tasks from conversation metadata."""
        return self.state_store.get_pending_tasks(conversation)

    async def _save_pending_tasks(self, conversation_id: str, tasks: list[Task]) -> None:
        """Persist the remaining task queue into conversation metadata."""
        await self.state_store.save_pending_tasks(conversation_id, tasks)

    async def _clear_pending_tasks(self, conversation_id: str) -> None:
        """Remove the queued tasks from conversation metadata."""
        await self.state_store.clear_pending_tasks(conversation_id)

    def _get_pending_follow_up(self, conversation: Conversation) -> dict[str, Any] | None:
        """Read the persisted follow-up payload from conversation metadata."""
        return self.state_store.get_pending_follow_up(conversation)

    def _get_pending_confirmation(self, conversation: Conversation) -> dict[str, Any] | None:
        """Read the persisted confirmation payload from conversation metadata."""
        return self.state_store.get_pending_confirmation(conversation)

    def _load_tasks_from_payload(self, raw_tasks: Any) -> list[Task]:
        """Parse a loose task list payload into validated tasks."""
        return self.state_store.load_tasks_from_payload(raw_tasks)

    def _render_completed_steps_text(self, completed_steps: Any) -> str:
        """Render previously completed steps for the intent prompt."""
        if not isinstance(completed_steps, list):
            return "无"

        normalized = [str(item).strip() for item in completed_steps if str(item).strip()]
        if not normalized:
            return "无"
        return " | ".join(self._truncate_text(item, 120) for item in normalized[:3])

    def _summarize_task_queue(self, tasks: list[Task]) -> str:
        """Summarize pending tasks in plain language for prompts and UI payloads."""
        if not tasks:
            return "无"

        lines: list[str] = []
        for index, task in enumerate(tasks[:5], start=1):
            tool_name = task.tool_name or "待定工具"
            target = str(
                task.target
                or task.parameters.get("section_type")
                or task.parameters.get("target_section")
                or task.parameters.get("title")
                or task.parameters.get("subtitle")
                or task.parameters.get("title_keyword")
                or ""
            ).strip()
            if not target and isinstance(task.parameters.get("slide_index"), int):
                target = f"第{int(task.parameters['slide_index']) + 1}页"
            action = str(task.action or "").strip()
            preview = str(task.proposed_content or task.response or task.parameters.get("question") or "").strip()
            parts = [task.type, tool_name]
            if action:
                parts.append(action)
            if target:
                parts.append(target)
            summary = " / ".join(parts)
            if preview:
                summary = f"{summary}: {self._truncate_text(preview, 80)}"
            lines.append(f"{index}. {summary}")
        if len(tasks) > 5:
            lines.append(f"其余 {len(tasks) - 5} 项待继续")
        return "；".join(lines)

    async def _save_pending_follow_up(
        self,
        conversation_id: str,
        follow_up: dict[str, Any],
    ) -> None:
        """Persist the current interruption point for a later resume."""
        await self.state_store.save_pending_follow_up(conversation_id, follow_up)

    async def _save_pending_confirmation(
        self,
        conversation_id: str,
        confirmation: dict[str, Any],
    ) -> None:
        """Persist the current confirmation request for a later confirm/cancel decision."""
        await self.state_store.save_pending_confirmation(conversation_id, confirmation)

    async def _clear_pending_follow_up(self, conversation_id: str) -> None:
        """Remove the stored follow-up interruption marker."""
        await self.state_store.clear_pending_follow_up(conversation_id)

    async def _clear_pending_confirmation(self, conversation_id: str) -> None:
        """Remove the stored confirmation interruption marker."""
        await self.state_store.clear_pending_confirmation(conversation_id)

    async def _update_conversation_metadata(self, conversation_id: str, changes: dict[str, Any | None]) -> None:
        """Merge conversation metadata updates using a fresh database session."""
        await self.state_store.update_metadata(conversation_id, changes)

    def _refresh_request_state(self, conversation_id: str) -> None:
        """Refresh cached plan and conversation rows in the request-scoped session."""
        plan = self.db.get(Plan, self.plan_id)
        if plan is not None:
            self.db.refresh(plan)

        conversation = self.db.get(Conversation, conversation_id)
        if conversation is not None:
            self.db.refresh(conversation)

    def _json_ready(self, value: Any) -> Any:
        """Convert arbitrary values into JSON-serializable data."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): self._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_ready(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._json_ready(value.model_dump())
        if hasattr(value, "__dict__"):
            return self._json_ready(value.__dict__)
        return str(value)

    def _serialize_plan(self, plan: Plan | None) -> dict[str, Any] | None:
        """Serialize a plan for SSE responses."""
        if plan is None:
            return None
        return {
            "id": plan.id,
            "title": plan.title,
            "doc_type": plan.doc_type,
            "subject": plan.subject,
            "grade": plan.grade,
            "content": plan.content,
            "metadata": plan.metadata_json,
        }

    def _chunk_text(self, text: str, chunk_size: int = 80) -> list[str]:
        """Split final text into small chunks for SSE streaming."""
        if not text:
            return [""]
        return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]

    def _format_sse(self, event: str, data: dict[str, Any]) -> str:
        """Encode a server-sent event payload."""
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
