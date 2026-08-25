"""A2A method registry — maps A2A method names to async handler callables.

Each entry in ``method_registry`` is a plain async callable with the signature::

    async def handler(payload: dict, request: Request, user: dict)
                      -> Task | StreamingResponse

Adding a new A2A method
-----------------------
Create a handler (or reuse an existing one) and register it::

    from aviator.a2a.handler import method_registry
    from my_plugin.handlers import my_handler

    method_registry.register("my/new-method", my_handler)
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request

from aviator.a2a.handler.request_handler import A2AChatHandler
from aviator.a2a.handler.tasks_handler import TasksHandler
from aviator.a2a.models import A2AMethod

# Type alias for any A2A method handler.
# Returns Any because streaming handlers return StreamingResponse while
# non-streaming handlers return Task (a Pydantic model).
A2AHandlerFunc = Callable[[dict, Request, dict], Awaitable[Any]]


# -- Registry ----------------------------------------------------------------


class MethodRegistry:
    """Maps A2A method names to async handler callables.

    Open for extension -- call ``register()`` to add new methods without
    touching existing registrations (OCP).
    """

    def __init__(self) -> None:
        """Initialise an empty handler registry."""
        self._registry: dict[str, A2AHandlerFunc] = {}

    def register(self, method: str, handler: A2AHandlerFunc) -> None:
        """Register *handler* under *method* name."""
        self._registry[method] = handler

    def get(self, method: str) -> A2AHandlerFunc | None:
        """Return the handler for *method*, or ``None`` if not registered."""
        return self._registry.get(method)

    def supported_methods(self) -> list[str]:
        """Return all currently registered method names."""
        return list(self._registry.keys())


# -- Module-level singleton wired with defaults ------------------------------

_chat_handler = A2AChatHandler()
_tasks_handler = TasksHandler()

method_registry = MethodRegistry()

# Primary A2A spec methods
method_registry.register(A2AMethod.MESSAGE_SEND, _chat_handler.handle_message_send)
method_registry.register(A2AMethod.MESSAGE_STREAM, _chat_handler.handle_message_stream)
method_registry.register(A2AMethod.TASKS_GET, _tasks_handler.handle_get)
method_registry.register(A2AMethod.TASKS_CANCEL, _tasks_handler.handle_cancel)
