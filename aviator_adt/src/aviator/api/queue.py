"""Queue status endpoint for Content Aviator API."""

import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Request,
    Security,
)
from opentelemetry import trace

from aviator.api.auth import require_authentication
from aviator.models import QueueStatusModel
from aviator.services.celery_status import get_celery_queue_status
from aviator.utils.limiter import limiter

# Lower httpx loglevel, used by google sdk
logging.getLogger("httpx").setLevel("WARN")

tracer = trace.get_tracer(__name__)
router = APIRouter(tags=["general"])
logger = logging.getLogger("aviator")


@router.get(
    "/queue-status",
    tags=["general"],
    responses={
        401: {"description": "Unauthorized"},
    },
    response_model_exclude_none=True,
)
@limiter.limit("15/minute")
def get_queue_status(
    request: Request,  # noqa: ARG001
    user: Annotated[dict[str, Any], Security(require_authentication("queue_status"))],  # noqa: ARG001
) -> QueueStatusModel:
    """Get the status of Content Aviator workers and job queues.

    Returns information about:
    - Active workers and their status
    - Registered task types
    - Broker connection status

    Requires authentication.
    """

    return get_celery_queue_status()
