"""Unit tests for plugin tools."""

from unittest.mock import Mock, patch

import pytest

from aviator_plugin_sample.basic_tool import greeting_tool


def test_greeting_tool_basic():
    """Test basic greeting functionality."""
    result = greeting_tool.invoke({"name": "Alice"})
    assert "Alice" in result
    assert isinstance(result, str)
    assert len(result) > 0


def test_greeting_tool_with_different_names():
    """Test greeting with various names."""
    names = ["Bob", "Charlie", "Diana"]
    for name in names:
        result = greeting_tool.invoke({"name": name})
        assert name in result
        assert isinstance(result, str)


def test_greeting_tool_empty_name():
    """Test greeting with empty name."""
    result = greeting_tool.invoke({"name": ""})
    assert result is not None
    assert isinstance(result, str)


# Weather Tool Tests
@pytest.mark.asyncio
async def test_weather_fetcher_success(sample_weather_data):
    """Test weather fetcher with mocked API response."""
    from aviator_plugin_sample.weather_fetcher import weather_fetcher

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = Mock()
        # Mock Open-Meteo API response format
        mock_response.json.return_value = {
            "current_weather": {"temperature": 15.0, "windspeed": 10.0, "weathercode": 0, "time": "2026-01-26T12:00"}
        }
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Use actual latitude/longitude (Toronto coordinates)
        result = await weather_fetcher.ainvoke({"latitude": 43.70, "longitude": -79.42})

        assert isinstance(result, str)
        assert len(result) > 0


@pytest.mark.asyncio
async def test_weather_fetcher_api_error():
    """Test weather fetcher handles API errors."""
    from aviator_plugin_sample.weather_fetcher import weather_fetcher

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = Exception("API Error")

        result = await weather_fetcher.ainvoke({"latitude": 43.70, "longitude": -79.42})

        # Tool should return error message, not raise exception
        assert isinstance(result, str)
        assert "error" in result.lower() or "failed" in result.lower()


@pytest.mark.asyncio
async def test_weather_fetcher_with_coordinates():
    """Test weather fetcher with valid coordinates."""
    from aviator_plugin_sample.weather_fetcher import weather_fetcher

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = Mock()
        mock_response.json.return_value = {
            "current_weather": {"temperature": 20.0, "windspeed": 5.0, "weathercode": 1, "time": "2026-01-26T15:00"}
        }
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = await weather_fetcher.ainvoke({"latitude": 51.5074, "longitude": -0.1278})

        assert isinstance(result, str)


# Calculator/Utility Tool Tests
def test_calculation_input_validation():
    """Test Pydantic model validation for calculator."""
    from aviator_plugin_sample.models import CalculationInput

    # Valid input
    calc = CalculationInput(operation="add", x=5, y=3)
    assert calc.operation == "add"
    assert calc.x == 5
    assert calc.y == 3

    # Test different operations
    operations = ["add", "subtract", "multiply", "divide"]
    for op in operations:
        calc = CalculationInput(operation=op, x=10, y=2)
        assert calc.operation == op


def test_calculation_input_invalid_operation():
    """Test Pydantic model rejects invalid operation."""
    from pydantic import ValidationError

    from aviator_plugin_sample.models import CalculationInput

    # Invalid operation should raise validation error
    with pytest.raises(ValidationError):
        CalculationInput(operation="invalid_op", x=5, y=3)


def test_utility_calculator_tool_addition():
    """Test utility calculator addition."""
    from aviator_plugin_sample.models import CalculationInput
    from aviator_plugin_sample.utility import calculator

    calc_input = CalculationInput(operation="add", x=5, y=3)
    result = calculator.invoke({"calculation": calc_input})

    assert result.result == 8.0
    assert result.operation == "add"
    assert "8" in result.expression


def test_utility_calculator_division_by_zero():
    """Test utility calculator handles division by zero."""
    from pydantic import ValidationError

    from aviator_plugin_sample.models import CalculationInput

    # Division by zero should be caught by Pydantic validation
    with pytest.raises(ValidationError) as exc_info:
        CalculationInput(operation="divide", x=5, y=0)

    assert "Cannot divide by zero" in str(exc_info.value)
