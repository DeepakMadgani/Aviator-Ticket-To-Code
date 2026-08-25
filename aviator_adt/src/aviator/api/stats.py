"""Usage stats API router."""

import logging
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Security, status
from pydantic import ValidationError

from aviator.api.auth import require_authentication
from aviator.services.usage_tracking.metrics import semantic_chunks_total, semantic_documents_total
from aviator.services.usage_tracking.models import StatsQueryParams, StatsResponseModel
from aviator.services.usage_tracking.queries import get_semantic_size, get_usage_stats
from aviator.settings import settings
from aviator.utils.limiter import limiter

_DATE_FORMAT = "YYYY-MM-DD"


def _parse_date_param(value: str | None, param_name: str) -> date | None:
    """Parse a date query parameter string into a date object.

    Raises:
        HTTPException: 400 if the value is not a valid date in YYYY-MM-DD format.

    """
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid value for '{param_name}': '{value}' is not a valid date (expected format: {_DATE_FORMAT})",
        ) from exc


router = APIRouter(prefix="/v1", tags=["stats"])
logger = logging.getLogger("aviator")


def _generate_date_buckets(from_date: date, to_date: date, units: str) -> list[str]:
    """Generate all date bucket keys for the requested time range."""
    keys: list[str] = []

    if units == "days":
        current = from_date
        while current <= to_date:
            keys.append(current.isoformat())
            current += timedelta(days=1)

    elif units == "months":
        year, month = from_date.year, from_date.month
        end_year, end_month = to_date.year, to_date.month
        while (year, month) <= (end_year, end_month):
            keys.append(f"{year:04d}-{month:02d}")
            month += 1
            if month > 12:
                month = 1
                year += 1

    elif units == "years":
        keys.extend(str(y) for y in range(from_date.year, to_date.year + 1))

    return keys


def _build_stats_series(tallies: list, from_date: date, to_date: date, units: str) -> list[dict]:
    """Build a zero-filled time series from tally records, mapping transaction types to response fields.

    Transaction-type → response-field mapping
    -----------------------------------------
    ``embedding_add``
        Increments ``embeddingsRequestCount``, ``chunksCount``, ``documentsEmbeddedCount``.
    ``embedding_update``
        If and only if there is content in the update, increments ``embeddingsRequestCount``,
        ``chunksCount``, ``documentsEmbeddedCount``. An update replaces an already embedded
        document, and has the same cost that adding a new embedding a new document would.
        This is only true if content exists in the update request; so it will only increment
        if there is content in the update. The net chunk delta is still recorded in the
        tally for ``get_semantic_size`` to consume.
    ``embedding_delete``
        Increments ``chunksDeletedCount`` and ``documentsDeletedCount`` only.
        Standalone deletes are intentionally excluded from ``embeddingsRequestCount``.
    """
    bucket_keys = _generate_date_buckets(from_date, to_date, units)

    # Index DB results by bucket key
    tally_map: dict[str, list] = {}
    for t in tallies:
        tally_map.setdefault(t.date, []).append(t)

    data: list[dict] = []
    for bucket_key in bucket_keys:
        row = {
            "date": bucket_key,
            "chatCount": 0,
            "directChatCount": 0,
            "embeddingsRequestCount": 0,
            "chunksCount": 0,
            "documentsEmbeddedCount": 0,
            "chunksDeletedCount": 0,
            "documentsDeletedCount": 0,
            "semanticQueryCount": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_total_requests": 0,
        }
        for tally in tally_map.get(bucket_key, []):
            if tally.transaction_type in ("chat", "chat_ws"):
                row["chatCount"] += tally.total_count
                row["input_tokens"] += tally.input_tokens
                row["output_tokens"] += tally.output_tokens
                row["llm_total_requests"] += tally.llm_total_requests
            elif tally.transaction_type == "direct_chat":
                row["directChatCount"] += tally.total_count
                row["input_tokens"] += tally.input_tokens
                row["output_tokens"] += tally.output_tokens
                row["llm_total_requests"] += tally.llm_total_requests
            elif tally.transaction_type in ("embedding_add", "embedding_update"):
                row["embeddingsRequestCount"] += tally.total_count
                row["chunksCount"] += tally.total_chunks
                row["documentsEmbeddedCount"] += tally.total_documents
            elif tally.transaction_type == "embedding_delete":
                # Standalone deletes only affect the delete counters.
                row["chunksDeletedCount"] += tally.total_chunks
                row["documentsDeletedCount"] += tally.total_documents
            elif tally.transaction_type == "search_query":
                row["semanticQueryCount"] += tally.total_count
        data.append(row)
    return data


@router.get(
    "/usage-stats",
    tags=["stats"],
    response_model_by_alias=True,
    responses={
        401: {"description": "Unauthorized"},
        400: {"description": "Bad request — invalid or conflicting query parameters"},
        501: {"description": "Feature disabled"},
    },
    summary="Get usage statistics",
    description=(
        "Retrieve usage statistics for a tenant over a date range. "
        "Returns zero-filled buckets grouped by day, month, or year, "
        "plus current semantic search footprint."
    ),
)
@limiter.limit("15/minute")
async def get_stats(
    request: Request,  # noqa: ARG001
    user: Annotated[dict[str, Any], Security(require_authentication("usage_stats"))],
    tenant_id: Annotated[str | None, Query(description="Tenant ID (falls back to auth context)")] = None,
    units: Annotated[Literal["days", "months", "years"], Query(description="Reporting time unit")] = "days",
    from_offset: Annotated[
        int | None, Query(le=0, description="Relative start offset in `units` from today (must be <= 0)")
    ] = None,
    to_offset: Annotated[
        int | None, Query(le=0, description="Relative end offset in `units` from today (must be <= 0, default: 0)")
    ] = None,
    from_date: Annotated[str | None, Query(description="Absolute start date YYYY-MM-DD (inclusive, UTC)")] = None,
    to_date: Annotated[str | None, Query(description="Absolute end date YYYY-MM-DD (inclusive, UTC)")] = None,
) -> StatsResponseModel:
    """Return usage statistics for the requested tenant and date range."""
    if not settings.usage_tracking_enabled:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Usage tracking is disabled",
        )

    # Validate date parameters early — returns 400 on bad format
    parsed_from_date = _parse_date_param(from_date, "from_date")
    parsed_to_date = _parse_date_param(to_date, "to_date")

    # Resolve tenant from param or auth context
    resolved_tenant = tenant_id or (user.get("tenantId") if user else None)

    try:
        params = StatsQueryParams(
            tenant_id=resolved_tenant,
            units=units,
            from_offset=from_offset,
            to_offset=to_offset,
            from_date=parsed_from_date,
            to_date=parsed_to_date,
        )
        resolved_from, resolved_to = params.resolve_date_range(
            max_query_days=settings.usage_tracking_max_query_days,
        )
    except ValidationError as e:
        detail = "; ".join(err["msg"] for err in e.errors())
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Query tallies and semantic size
    tallies = await get_usage_stats(resolved_tenant, resolved_from, resolved_to, units)
    semantic = await get_semantic_size(resolved_tenant)

    # Build zero-filled time series
    data = _build_stats_series(tallies, resolved_from, resolved_to, units)

    # Update Prometheus gauges with latest semantic size snapshot
    tenant_label = resolved_tenant or settings.default_schema
    semantic_documents_total.labels(tenant_id=tenant_label).set(semantic.total_documents)
    semantic_chunks_total.labels(tenant_id=tenant_label).set(semantic.total_chunks)

    return StatsResponseModel(
        tenant_id=resolved_tenant or settings.default_schema,
        timezone="UTC",
        units=units,
        from_date=resolved_from.isoformat(),
        to_date=resolved_to.isoformat(),
        data=data,
        semantic_size={
            "documentsEmbeddedTotal": semantic.total_documents,
            "chunksTotal": semantic.total_chunks,
        },
    )
