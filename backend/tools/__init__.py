"""Public exports for the tools module."""

from __future__ import annotations

from .decorator import register_tool, tool
from .executor import ToolExecutor
from .models import Tool, ToolError, ToolExecutionError, ToolNotFoundError, ToolValidationError
from .registry import ToolsRegistry, default_registry


@tool(register=True)
def echo(message: str) -> str:
    """Return the input message unchanged."""
    return message


@tool(register=True)
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


__all__ = [
    "Tool",
    "ToolError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    "ToolsRegistry",
    "ToolExecutor",
    "default_registry",
    "register_tool",
    "tool",
    "echo",
    "add",
]
