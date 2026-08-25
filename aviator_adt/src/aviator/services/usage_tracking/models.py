"""Pydantic models for usage tracking data and API request/response."""

import calendar
import datetime
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StatsQueryParams(BaseModel):
    """Query parameters for the stats API endpoint."""

    tenant_id: str | None = Field(default=None, description="Tenant to query (None = default/public)")
    units: Literal["days", "months", "years"] = Field(default="days", description="Reporting time unit")
    from_offset: int | None = Field(
        default=None,
        le=0,
        description="Relative offset from today in `units` (e.g., -30). Must be <= 0. Mutually exclusive with `from_date`",
    )
    to_offset: int | None = Field(
        default=None,
        le=0,
        description="Relative offset from today in `units` (e.g., 0 = today). Must be <= 0. Mutually exclusive with `to_date`",
    )
    from_date: date | None = Field(
        default=None,
        description="Absolute start date (inclusive, UTC). Must not be in the future. Mutually exclusive with `from_offset`",
    )
    to_date: date | None = Field(
        default=None,
        description="Absolute end date (inclusive, UTC). Must not be in the future. Mutually exclusive with `to_offset`",
    )

    def resolve_date_range(self, max_query_days: int = 1000) -> tuple[date, date]:
        """Resolve the effective from_date and to_date from offsets or explicit dates.

        Returns:
            Tuple of (from_date, to_date).

        Raises:
            ValueError: If conflicting parameters are provided or the resolved
                date range exceeds *max_query_days*.

        """
        if self.from_offset is not None and self.from_date is not None:
            msg = "Cannot specify both 'from_offset' and 'from_date'"
            raise ValueError(msg)

        if self.to_offset is not None and self.to_date is not None:
            msg = "Cannot specify both 'to_offset' and 'to_date'"
            raise ValueError(msg)

        today = datetime.datetime.now(tz=datetime.UTC).date()

        # Reject explicit dates in the future
        if self.from_date is not None and self.from_date > today:
            msg = "from_date must not be in the future"
            raise ValueError(msg)
        if self.to_date is not None and self.to_date > today:
            msg = "to_date must not be in the future"
            raise ValueError(msg)

        # Resolve to_date
        if self.to_date is not None:
            resolved_to = self.to_date
        elif self.to_offset is not None:
            resolved_to = _apply_offset(today, self.to_offset, self.units)
        else:
            resolved_to = today

        # Resolve from_date
        if self.from_date is not None:
            resolved_from = self.from_date
        elif self.from_offset is not None:
            resolved_from = _apply_offset(today, self.from_offset, self.units)
        else:
            resolved_from = resolved_to

        # Snap to period boundaries for coarser granularity
        if self.units == "months":
            resolved_from = resolved_from.replace(day=1)
            last_day = calendar.monthrange(resolved_to.year, resolved_to.month)[1]
            resolved_to = resolved_to.replace(day=last_day)
        elif self.units == "years":
            resolved_from = resolved_from.replace(month=1, day=1)
            resolved_to = resolved_to.replace(month=12, day=31)

        # Cap resolved dates to today (offsets/snapping could push to_date into the future)
        resolved_to = min(resolved_to, today)
        if resolved_from > today:
            msg = "Resolved from_date must not be in the future"
            raise ValueError(msg)

        if resolved_from > resolved_to:
            msg = "from_date must be before or equal to to_date"
            raise ValueError(msg)

        delta = (resolved_to - resolved_from).days
        if delta > max_query_days:
            msg = f"Date range of {delta} days exceeds maximum allowed ({max_query_days} days)"
            raise ValueError(msg)

        return resolved_from, resolved_to


def _apply_offset(base: date, offset: int, units: str) -> date:
    """Apply a relative offset to a base date."""
    if units == "days":
        return base + timedelta(days=offset)

    if units == "months":
        month = base.month + offset
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        max_day = calendar.monthrange(year, month)[1]
        day = min(base.day, max_day)
        return date(year, month, day)

    if units == "years":
        year = base.year + offset
        max_day = calendar.monthrange(year, base.month)[1]
        day = min(base.day, max_day)
        return date(year, base.month, day)

    msg = f"Unsupported units: {units}"
    raise ValueError(msg)


class DailyTallyModel(BaseModel):
    """A single tally record for a time period and transaction type."""

    date: str
    transaction_type: str
    total_count: int = 0
    total_documents: int = 0
    total_chunks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_total_requests: int = 0


class SemanticSizeModel(BaseModel):
    """Semantic size metrics for a tenant's vector store."""

    total_documents: int = 0
    total_chunks: int = 0


class StatsResponseModel(BaseModel):
    """Response model for the stats API endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str = Field(..., alias="tenantId")
    timezone: str = "UTC"
    units: str
    from_date: str = Field(..., alias="from")
    to_date: str = Field(..., alias="to")
    data: list[dict]
    semantic_size: dict = Field(..., alias="semanticSize")
