"""Tool registry implementations."""

from __future__ import annotations

import logging
from typing import Any

from .models import Tool, ToolNotFoundError

logger = logging.getLogger(__name__)


class ToolsRegistry:
    """Registry that stores tools by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool and overwrite existing entries with a warning."""
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool registration for '%s'.", tool.name)
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        del self._tools[name]

    def get_tool(self, name: str) -> Tool:
        """Fetch a registered tool by name."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.") from exc

    def list_tools(self) -> list[Tool]:
        """Return a copy of the registered tools."""
        return list(self._tools.values())

    def get_openai_tools_schema(self) -> list[dict[str, Any]]:
        """Render registered tools into OpenAI function-calling schema."""
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            parameters = (
                tool.args_schema.model_json_schema()
                if hasattr(tool.args_schema, "model_json_schema")
                else tool.args_schema.schema()
            )
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters,
                    },
                }
            )
        return schemas


default_registry = ToolsRegistry()
