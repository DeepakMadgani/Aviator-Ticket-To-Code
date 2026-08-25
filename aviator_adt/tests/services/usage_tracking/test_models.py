"""Tests for usage tracking models."""

import datetime
from datetime import date, timedelta

import pytest

from aviator.services.usage_tracking.models import (
    StatsQueryParams,
    _apply_offset,
)


class TestApplyOffset:
    """Tests for the _apply_offset helper."""

    def test_days_positive(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, 5, "days") == date(2024, 6, 20)

    def test_days_negative(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, -30, "days") == date(2024, 5, 16)

    def test_months_positive(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, 2, "months") == date(2024, 8, 15)

    def test_months_negative(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, -3, "months") == date(2024, 3, 15)

    def test_months_wraps_year_forward(self):
        base = date(2024, 11, 10)
        assert _apply_offset(base, 3, "months") == date(2025, 2, 10)

    def test_months_wraps_year_backward(self):
        base = date(2024, 2, 10)
        assert _apply_offset(base, -3, "months") == date(2023, 11, 10)

    def test_months_clamps_day(self):
        # March 31 - 1 month -> Feb doesn't have 31 days in 2024 (leap year, so 29)
        base = date(2024, 3, 31)
        assert _apply_offset(base, -1, "months") == date(2024, 2, 29)

    def test_months_clamps_day_non_leap(self):
        base = date(2023, 3, 31)
        assert _apply_offset(base, -1, "months") == date(2023, 2, 28)

    def test_years_positive(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, 1, "years") == date(2025, 6, 15)

    def test_years_negative(self):
        base = date(2024, 6, 15)
        assert _apply_offset(base, -2, "years") == date(2022, 6, 15)

    def test_years_clamps_leap_day(self):
        # Feb 29 - 1 year -> 2023 is not a leap year
        base = date(2024, 2, 29)
        assert _apply_offset(base, -1, "years") == date(2023, 2, 28)

    def test_unsupported_units_raises(self):
        with pytest.raises(ValueError, match="Unsupported units"):
            _apply_offset(date(2024, 1, 1), 1, "weeks")


class TestStatsQueryParams:
    """Tests for StatsQueryParams model and resolve_date_range."""

    def test_defaults_resolve_to_today(self):
        params = StatsQueryParams()
        from_date, to_date = params.resolve_date_range()
        today = datetime.datetime.now(tz=datetime.UTC).date()
        assert from_date == today
        assert to_date == today

    def test_from_offset_only(self):
        params = StatsQueryParams(from_offset=-7, units="days")
        from_date, to_date = params.resolve_date_range()
        today = datetime.datetime.now(tz=datetime.UTC).date()
        assert from_date == today - timedelta(days=7)
        assert to_date == today

    def test_both_offsets(self):
        params = StatsQueryParams(from_offset=-30, to_offset=0, units="days")
        from_date, to_date = params.resolve_date_range()
        today = datetime.datetime.now(tz=datetime.UTC).date()
        assert from_date == today - timedelta(days=30)
        assert to_date == today

    def test_explicit_dates(self):
        f = date(2024, 1, 1)
        t = date(2024, 1, 31)
        params = StatsQueryParams(from_date=f, to_date=t)
        from_date, to_date = params.resolve_date_range()
        assert from_date == f
        assert to_date == t

    def test_from_offset_and_from_date_conflict(self):
        params = StatsQueryParams(from_offset=-7, from_date=date(2024, 1, 1))
        with pytest.raises(ValueError, match="Cannot specify both"):
            params.resolve_date_range()

    def test_to_offset_and_to_date_conflict(self):
        params = StatsQueryParams(to_offset=0, to_date=date(2024, 1, 31))
        with pytest.raises(ValueError, match="Cannot specify both"):
            params.resolve_date_range()

    def test_from_after_to_raises(self):
        params = StatsQueryParams(from_date=date(2024, 2, 1), to_date=date(2024, 1, 1))
        with pytest.raises(ValueError, match="from_date must be before"):
            params.resolve_date_range()

    def test_exceeds_max_query_days(self):
        params = StatsQueryParams(from_date=date(2020, 1, 1), to_date=date(2024, 12, 31))
        with pytest.raises(ValueError, match="exceeds maximum"):
            params.resolve_date_range(max_query_days=365)

    def test_months_offsets(self):
        params = StatsQueryParams(from_offset=-3, to_offset=0, units="months")
        from_date, to_date = params.resolve_date_range()
        today = datetime.datetime.now(tz=datetime.UTC).date()
        assert from_date < today
        assert to_date == today

    def test_positive_from_offset_rejected(self):
        with pytest.raises(ValueError):
            StatsQueryParams(from_offset=1, units="days")

    def test_positive_to_offset_rejected(self):
        with pytest.raises(ValueError):
            StatsQueryParams(to_offset=1, units="days")

    def test_future_from_date_rejected(self):
        future = datetime.datetime.now(tz=datetime.UTC).date() + timedelta(days=1)
        params = StatsQueryParams(from_date=future, to_date=future)
        with pytest.raises(ValueError, match="from_date must not be in the future"):
            params.resolve_date_range()

    def test_future_to_date_rejected(self):
        future = datetime.datetime.now(tz=datetime.UTC).date() + timedelta(days=1)
        params = StatsQueryParams(to_date=future)
        with pytest.raises(ValueError, match="to_date must not be in the future"):
            params.resolve_date_range()
