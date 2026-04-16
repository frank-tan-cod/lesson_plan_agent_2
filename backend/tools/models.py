"""Core models and exceptions for tool definitions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Type

from pydantic import BaseModel, ConfigDict, Field


class ToolError(Exception):
    """Base error for the tools module."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool does not exist in the registry."""


class ToolValidationError(ToolError):
    """Raised when tool arguments fail schema validation."""


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""


class Tool(BaseModel):
    """Structured definition for an executable tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(..., description="Unique tool name.")
    description: str = Field(..., description="Tool description for the LLM.")
    args_schema: Type[BaseModel] = Field(..., description="Pydantic arguments schema.")
    func: Callable = Field(..., description="Sync or async callable to execute.")
    return_direct: bool = Field(default=False, description="Whether the result should be returned directly.")

    def __hash__(self) -> int:
        """Hash tools by their unique name."""
        return hash(self.name)
