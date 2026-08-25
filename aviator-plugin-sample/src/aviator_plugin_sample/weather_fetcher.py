"""Weather_fetcher - Fetches real weather data using Open-Meteo API."""

import logging

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes (WW)
WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


@tool
async def weather_fetcher(latitude: float, longitude: float) -> str:
    """Get current weather information for any location using coordinates.

    Use this tool when the user asks about weather conditions for a specific location.
    Fetches real-time weather data from Open-Meteo API including temperature, wind speed,
    weather conditions, and observation time.

    Args:
        latitude: The latitude coordinate of the location (e.g., 6.9271 for Colombo)
        longitude: The longitude coordinate of the location (e.g., 79.8612 for Colombo)
        location_name: Optional name of the location for display (e.g., "Colombo, Sri Lanka")

    Returns:
        Current weather information including temperature, wind speed, conditions, and time

    Example:
        >>> await weather_fetcher(6.9271, 79.8612)
        "Weather in Colombo: 28°C (82°F), Clear sky, Wind: 15 km/h, Observed at: 2026-01-06T14:00"

    """
    logger.info("Weather_fetcher called for (lat: %s, lon: %s)", latitude, longitude)

    # Build the API URL
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": latitude, "longitude": longitude, "current_weather": True}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        # Extract current weather data
        current = data.get("current_weather", {})
        temperature_c = current.get("temperature")
        windspeed_kmh = current.get("windspeed")
        weathercode = current.get("weathercode", 0)
        observation_time = current.get("time")

        # Convert temperature to Fahrenheit
        temperature_f = (temperature_c * 9 / 5) + 32 if temperature_c is not None else None

        # Get weather condition description
        weather_condition = WEATHER_CODES.get(weathercode, "Unknown")

        # Format the response
        result = (
            f"Temperature: {temperature_c}°C ({temperature_f:.1f}°F)\n"
            f"Conditions: {weather_condition}\n"
            f"Wind Speed: {windspeed_kmh} km/h\n"
            f"Observed at: {observation_time}"
        )

        logger.info("Weather data retrieved successfully for (lat: %s, lon: %s)", latitude, longitude)
        return result

    except httpx.HTTPError as e:
        error_msg = f"Error fetching weather data for (lat: {latitude}, lon: {longitude}): {e!s}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Unexpected error: {e!s}"
        logger.error(error_msg)
        return error_msg
