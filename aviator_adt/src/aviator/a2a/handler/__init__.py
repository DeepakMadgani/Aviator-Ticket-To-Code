"""Public surface of the ``aviator.a2a.handler`` package."""

from aviator.a2a.handler.error_handler import jsonrpc_error
from aviator.a2a.handler.registry import A2AHandlerFunc, MethodRegistry, method_registry
from aviator.a2a.handler.request_handler import A2AChatHandler
from aviator.a2a.handler.tasks_handler import TasksHandler
from aviator.exceptions import TaskNotCancelableError, TaskNotFoundError

__all__ = [
    "A2AChatHandler",
    "A2AHandlerFunc",
    "MethodRegistry",
    "TaskNotCancelableError",
    "TaskNotFoundError",
    "TasksHandler",
    "jsonrpc_error",
    "method_registry",
]
