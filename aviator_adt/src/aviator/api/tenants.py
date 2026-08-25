"""Tenant management API router.

Provides CRUD endpoints for managing tenant schemas in PostgreSQL.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Security, status

from aviator.api.auth import require_authentication
from aviator.exceptions import TenantAlreadyExistsError, TenantNotFoundError
from aviator.services.tenant import TenantInfo, tenant_service
from aviator.utils.limiter import limiter
from aviator.vector_store import vector_store

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])
logger = logging.getLogger("aviator")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Unauthorized"},
        409: {"description": "Tenant already exists"},
    },
)
@limiter.limit("15/minute")
async def create_tenant(
    request: Request,  # noqa: ARG001
    tenant_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication("tenant_create"))],  # noqa: ARG001
) -> TenantInfo:
    """Create a new tenant with a dedicated PostgreSQL schema.

    The schema ``tenant_{tenant_id}`` is created along with the vector store
    table and HNSW index.
    """
    try:
        return tenant_service.create_tenant(tenant_id)
    except TenantAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e


@router.get(
    "",
    responses={
        401: {"description": "Unauthorized"},
    },
)
@limiter.limit("15/minute")
async def list_tenants(
    request: Request,  # noqa: ARG001
    user: Annotated[dict[str, Any], Security(require_authentication("tenant_list"))],  # noqa: ARG001
) -> list[TenantInfo]:
    """List all registered tenants."""
    return tenant_service.list_tenants()


@router.get(
    "/{tenant_id}",
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Tenant not found"},
    },
)
@limiter.limit("15/minute")
async def get_tenant(
    request: Request,  # noqa: ARG001
    tenant_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication("tenant_get"))],  # noqa: ARG001
) -> TenantInfo:
    """Get information about a specific tenant."""
    try:
        return tenant_service.get_tenant(tenant_id)
    except TenantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Tenant not found"},
    },
)
@limiter.limit("15/minute")
async def delete_tenant(
    request: Request,  # noqa: ARG001
    tenant_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication("tenant_delete"))],  # noqa: ARG001
) -> None:
    """Delete a tenant and drop its entire PostgreSQL schema (CASCADE).

    **Warning:** This permanently deletes all data for the tenant.
    """
    try:
        tenant_service.delete_tenant(tenant_id)
        # Evict from the vector store cache
        from aviator.services.tenant import tenant_id_to_schema_name

        vector_store.remove_tenant_store(tenant_id_to_schema_name(tenant_id))
    except TenantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
