"""Tool execution helpers."""

from __future__ import annotations

import inspect
from typing import Any

from pydantic import ValidationError

from .cancellation import ToolCancelledError
from .models import ToolExecutionError, ToolNotFoundError, ToolValidationError
from .registry import ToolsRegistry


class ToolExecutor:
    """Validate and execute tools from a registry."""

    def __init__(self, registry: ToolsRegistry) -> None:
        self.registry = registry

    async def execute(self, tool_name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Execute a tool with validated arguments."""
        payload = dict(arguments or {})
        payload.update(kwargs)

        try:
            tool = self.registry.get_tool(tool_name)
        except ToolNotFoundError:
            raise

        try:
            validated_args = tool.args_schema(**payload)
        except ValidationError as exc:
            raise ToolValidationError(f"Invalid arguments for tool '{tool_name}': {exc}") from exc

        call_arguments = validated_args.model_dump()
        signature = inspect.signature(tool.func)
        accepts_var_keyword = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        )
        if accepts_var_keyword:
            for key, value in payload.items():
                if key not in call_arguments:
                    call_arguments[key] = value
        else:
            for name in signature.parameters:
                if name not in call_arguments and name in payload:
                    call_arguments[name] = payload[name]

        try:
            if inspect.iscoroutinefunction(tool.func):
                return await tool.func(**call_arguments)
            # Keep sync tools usable without requiring callers to manage two APIs.
            # Direct invocation is simpler and avoids thread-pool shutdown issues.
            return tool.func(**call_arguments)
        except ToolCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolExecutionError(f"Tool '{tool_name}' execution failed: {exc}") from exc
