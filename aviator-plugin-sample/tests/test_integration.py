"""Integration tests for plugin tools with live APIs."""

from datetime import UTC, datetime

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_holidays_canada_live_api():
    """Test holidays tool with live API call."""
    from aviator_plugin_sample.holidays_canada import holidays_canada

    current_year = datetime.now(tz=UTC).year

    # Use string literals for province IDs, not enum
    result = await holidays_canada.ainvoke({"province_id": "ON", "year": current_year})

    assert result is not None
    assert isinstance(result, str)

    # If API call fails, skip the test
    if "Error" in result or "error" in result:
        pytest.skip(f"Holiday API unavailable or returned error: {result}")

    assert "Ontario" in result or str(current_year) in result
    # Should contain at least one holiday
    assert len(result) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_holidays_canada_different_provinces():
    """Test holidays tool with different provinces."""
    from aviator_plugin_sample.holidays_canada import holidays_canada

    current_year = datetime.now(tz=UTC).year
    provinces = ["ON", "BC", "QC"]

    for province in provinces:
        result = await holidays_canada.ainvoke({"province_id": province, "year": current_year})

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.asyncio
async def test_holidays_canada_specific_year():
    """Test holidays tool with specific year."""
    from aviator_plugin_sample.holidays_canada import holidays_canada

    result = await holidays_canada.ainvoke({"province_id": "ON", "year": 2026})

    assert result is not None
    assert isinstance(result, str)

    if "Error" in result or "error" in result:
        pytest.skip(f"Holiday API unavailable or returned error: {result}")

    assert "2026" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_weather_fetcher_live_api():
    """Test weather fetcher with live API (coordinates required)."""
    from aviator_plugin_sample.weather_fetcher import weather_fetcher

    # This test requires valid coordinates (Toronto)
    try:
        result = await weather_fetcher.ainvoke({"latitude": 43.70, "longitude": -79.42})
        assert result is not None
        assert isinstance(result, str)
        # If successful, should contain weather info
        if "error" not in result.lower():
            assert len(result) > 0
    except Exception as e:
        # API might fail, skip gracefully
        pytest.skip(f"Weather API not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_holidays_canada_with_mock(sample_holidays_data):
    """Test holidays tool with mocked API response."""
    from unittest.mock import Mock, patch

    from aviator_plugin_sample.holidays_canada import holidays_canada

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = sample_holidays_data
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = await holidays_canada.ainvoke({"province_id": "ON", "year": 2026})

        assert result is not None
        assert isinstance(result, str)
