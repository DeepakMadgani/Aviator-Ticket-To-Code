"""DevTools API router — only registered when DEV_TOOLS is enabled."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Security

from aviator import models
from aviator.api.auth import require_authentication
from aviator.services.devtools import check_document_summary_exists, get_document_chunk_count
from aviator.services.tenant import tenant_id_to_schema_name

logger = logging.getLogger("aviator")
router = APIRouter(prefix="/v1", tags=["devtools"])


@router.get(
    "/document-chunks/{document_id}",
    responses={
        401: {"description": "Unauthorized"},
    },
)
async def get_document_chunks(
    document_id: str,
    user: Annotated[dict[str, Any], Security(require_authentication("document_chunks"))],
) -> models.DocumentChunksResponseModel:
    """Check whether vector embeddings exist for a document and return the chunk count."""

    tenant_id = user.get("tenantId") if user else None
    schema_name = tenant_id_to_schema_name(tenant_id)

    chunk_count = await get_document_chunk_count(schema_name, document_id)
    summary = await check_document_summary_exists(schema_name, document_id)

    return models.DocumentChunksResponseModel(
        document_id=document_id,
        exists=chunk_count > 0,
        chunk_count=chunk_count,
        summary=summary,
    )
