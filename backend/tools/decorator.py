"""Decorator helpers for building Tool instances."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model

from .models import Tool
from .registry import ToolsRegistry, default_registry

F = TypeVar("F", bound=Callable[..., Any])


class _GeneratedArgsModel(BaseModel):
    """Base class for dynamically generated args schemas."""

    model_config = ConfigDict(extra="forbid")


def _build_args_schema(func: Callable[..., Any]) -> type[BaseModel]:
    """Create a Pydantic model from a callable signature."""
    signature = inspect.signature(func)
    field_definitions: dict[str, tuple[Any, Any]] = {}

    for parameter in signature.parameters.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise TypeError(f"Unsupported parameter kind for tool '{func.__name__}': {parameter.kind!s}")

        annotation = parameter.annotation if parameter.annotation is not inspect.Signature.empty else Any
        default = parameter.default if parameter.default is not inspect.Signature.empty else ...
        field_definitions[parameter.name] = (annotation, Field(default=default))

    model_name = f"{func.__name__.title().replace('_', '')}Args"
    return create_model(model_name, __base__=_GeneratedArgsModel, **field_definitions)


def _create_tool(
    func: F,
    *,
    args_schema: type[BaseModel] | None = None,
    name: str | None = None,
    description: str | None = None,
    return_direct: bool = False,
) -> Tool:
    """Build a Tool instance from a callable."""
    resolved_description = description or inspect.getdoc(func) or f"Tool '{func.__name__}'."
    resolved_schema = args_schema or _build_args_schema(func)
    return Tool(
        name=name or func.__name__,
        description=resolved_description,
        args_schema=resolved_schema,
        func=func,
        return_direct=return_direct,
    )


def tool(
    func: F | None = None,
    *,
    args_schema: type[BaseModel] | None = None,
    name: str | None = None,
    description: str | None = None,
    return_direct: bool = False,
    register: bool = False,
    registry: ToolsRegistry | None = None,
) -> Tool | Callable[[F], Tool]:
    """Create a Tool from a function, optionally registering it."""

    def decorator(inner_func: F) -> Tool:
        built_tool = _create_tool(
            inner_func,
            args_schema=args_schema,
            name=name,
            description=description,
            return_direct=return_direct,
        )
        if register:
            (registry or default_registry).register(built_tool)
        return built_tool

    if func is not None:
        return decorator(func)
    return decorator


def register_tool(tool_obj: Tool, registry: ToolsRegistry | None = None) -> Tool:
    """Register an existing Tool into the target registry."""
    (registry or default_registry).register(tool_obj)
    return tool_obj
