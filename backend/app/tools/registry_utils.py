"""Helpers for registering tool groups without duplicate boilerplate."""

from __future__ import annotations

from collections.abc import Iterable

from backend.tools import Tool
from backend.tools.registry import ToolsRegistry


def register_tools_once(registry: ToolsRegistry, tools: Iterable[Tool]) -> ToolsRegistry:
    """Register tools that are not already present in the target registry."""
    existing_names = {tool.name for tool in registry.list_tools()}
    for tool in tools:
        if tool.name in existing_names:
            continue
        registry.register(tool)
        existing_names.add(tool.name)
    return registry
