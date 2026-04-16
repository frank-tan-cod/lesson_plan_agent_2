"""Cancellation primitives shared between editor runtimes and tools."""

from __future__ import annotations

from threading import Event


class ToolCancelledError(RuntimeError):
    """Raised when a streaming editor request has been cancelled."""


class CancellationToken:
    """Thread-safe cancellation flag for sync and async tool execution."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """Mark the request as cancelled."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Abort the current execution when the request has been cancelled."""
        if self.is_cancelled():
            raise ToolCancelledError("request cancelled")
