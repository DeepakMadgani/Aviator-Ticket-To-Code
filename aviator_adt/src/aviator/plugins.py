"""Define plugins load graph modifiers."""

import importlib.metadata
import inspect
import logging
from collections.abc import Callable
from functools import cache

from opentelemetry import trace

from aviator.models import EmbeddingRequest
from aviator.settings import settings

tracer = trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


def get_auth_handler(endpoint: str | None = None) -> Callable | None:
    """Discover entry points under group 'aviator.auth_handlers'."""
    logger.info("Content System: %s", settings.content_system)

    # Get all available entry points in the auth_handlers group
    auth_eps = importlib.metadata.entry_points().select(group="aviator.auth_handlers")
    available_names = [ep.name for ep in auth_eps]
    logger.debug("Available auth handler entry points: %s", available_names)

    for ep in auth_eps:
        logger.debug("Entrypoint Auth Configuration for Content System '%s': %s", settings.content_system, ep.name)
        if ep.name == settings.content_system:
            logger.info("Entrypoint Auth Configuration found, loading: %s", ep.value)
            try:
                handler = ep.load()
            except Exception as e:
                logger.error("Failed to load auth handler: %s", e)
                import traceback

                traceback.print_exc()
                raise
            else:
                logger.info("Successfully loaded auth handler: %s", handler)

                # check if handler accepts 'endpoint' argument and invoke accordingly
                sig = inspect.signature(handler)
                if "endpoint" in sig.parameters:
                    logger.info("Auth handler accepts 'endpoint' argument, invoking with endpoint=%s", endpoint)
                    return handler(endpoint=endpoint)
                return handler

    logger.warning("No matching auth handler found for content_system='%s'", settings.content_system)
    return None


@cache
def get_auth_handler_ws() -> Callable | None:
    """Discover entry points under group 'aviator.auth_handlers_ws'."""

    for ep in importlib.metadata.entry_points().select(group="aviator.auth_handlers_ws"):
        if ep.name == settings.content_system:
            return ep.load()

    return None


@cache
def load_rag_permission_filters() -> list[tuple[str, callable]]:
    """Discover entry points under group 'aviator.rag_permission_filters'."""

    eps = importlib.metadata.entry_points().select(group="aviator.rag_permission_filters")
    return [(ep.name, ep.load()) for ep in eps]


@cache
def get_rag_metadata_retriever() -> Callable | None:
    """Discover entry points under group 'aviator.rag_metadata_retrievers'."""

    for ep in importlib.metadata.entry_points().select(group="aviator.rag_metadata_retrievers"):
        if ep.name == settings.content_system:
            return ep.load()

    return None


@cache
def load_startup_extension() -> list:
    """Discover entry points under group 'aviator.startup_extensions'."""

    eps = importlib.metadata.entry_points().select(group="aviator.startup_extensions")
    return [(ep.dist.name, ep.name, ep.load()) for ep in eps]


@cache
def load_graph_modifiers() -> list:
    """Discover entry points under group 'aviator.graph_extensions'."""

    eps = importlib.metadata.entry_points().select(group="aviator.graph_extensions")
    return [ep.load() for ep in eps]


@cache
def load_tool_modifiers() -> list:
    """Discover entry points under group 'aviator.tool_modifiers'."""

    eps = importlib.metadata.entry_points().select(group="aviator.tool_modifiers")
    return [ep.load() for ep in eps]


def load_prompt_modifiers(**kwargs: dict) -> list:
    """Discover entry points under group 'aviator.prompt_modifiers'."""

    eps = importlib.metadata.entry_points().select(group="aviator.prompt_modifiers")

    for fn in [ep.load() for ep in eps]:
        fn(**kwargs)


def load_embedding_extension(request: EmbeddingRequest, is_metadata: bool = False) -> list:
    """Discover entry points under group 'aviator.embedding_extensions'."""

    eps = importlib.metadata.entry_points().select(group="aviator.embedding_extensions")
    for ep in eps:
        with tracer.start_as_current_span(
            "embedding_extension:" + ep.name, attributes={"extension": ep.value, "is_metadata": str(is_metadata)}
        ) as span:
            if "workspaceID" in request.metadata:
                span.set_attribute("workspace_id", str(request.metadata["workspaceID"]))
            if "documentID" in request.metadata:
                span.set_attribute("document_id", str(request.metadata["documentID"]))

            fn = ep.load()
            fn(request=request, is_metadata=is_metadata)


@cache
def load_routers() -> list:
    """Discover entry points under group 'aviator.routers'."""

    eps = importlib.metadata.entry_points().select(group="aviator.routers")
    return [ep.load() for ep in eps]


@cache
def list_plugins() -> list[str]:
    """Return a list of distribution names that define entry points in the given group."""

    eps = importlib.metadata.entry_points()

    # Flatten into a single iterable of EntryPoint objects:
    all_eps = list(eps) if hasattr(eps, "select") else [ep for lst in eps.groups.values() for ep in lst]

    # Filter for any group starting with 'aviator'
    aviator_eps = [ep for ep in all_eps if ep.group.startswith("aviator")]

    # Collect and dedupe the distribution (package) names
    dists = {f"{ep.dist.name} {importlib.metadata.version(ep.dist.name)}" for ep in aviator_eps}
    return sorted(dists)


@cache
def load_plugin_celery_imports() -> tuple[str, ...]:
    """Discover entry points under group 'aviator.celery_imports'."""
    eps = importlib.metadata.entry_points().select(group="aviator.celery_imports")
    return tuple(ep.value for ep in eps)


@cache
def load_beat_schedules() -> dict:
    """Discover entry points under group 'aviator.celery_beat_schedules'.

    Each entry point must resolve to a callable that returns a ``dict``
    whose keys are schedule names and values are Celery beat schedule
    definitions (task, schedule, args, etc.).
    """
    eps = importlib.metadata.entry_points().select(group="aviator.celery_beat_schedules")
    schedules: dict = {}
    for ep in eps:
        fn = ep.load()
        plugin_schedules = fn()
        if isinstance(plugin_schedules, dict):
            schedules.update(plugin_schedules)
    return schedules


@cache
def load_mcp_server_plugins() -> list:
    """Discover entry points under group 'aviator.mcp_servers'."""
    eps = importlib.metadata.entry_points().select(group="aviator.mcp_servers")
    return [ep.load() for ep in eps]


@cache
def load_search_filter_extensions() -> list:
    """Discover entry points under group 'aviator.search_filter_extensions'."""

    eps = importlib.metadata.entry_points().select(group="aviator.search_filter_extensions")
    return [ep.load() for ep in eps]
