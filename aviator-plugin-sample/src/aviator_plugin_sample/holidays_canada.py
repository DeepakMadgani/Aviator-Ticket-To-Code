"""Canadian Holidays tool - Get holiday information for Canadian provinces."""

import logging
from datetime import UTC, datetime
from typing import Literal

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Type alias for Canadian provinces
ProvinceId = Literal["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"]


@tool
async def holidays_canada(province_id: ProvinceId, year: int | None = None) -> str:
    """Get holiday information for a Canadian province.

    Use this tool when the user asks about holidays in Canada or a specific Canadian province.
    Returns information about all holidays in the province for the specified year,
    including the next upcoming holiday.

    Args:
        province_id: Two-letter province code (AB=Alberta, BC=British Columbia,
                    MB=Manitoba, NB=New Brunswick, NL=Newfoundland and Labrador,
                    NS=Nova Scotia, NT=Northwest Territories, NU=Nunavut,
                    ON=Ontario, PE=Prince Edward Island, QC=Quebec,
                    SK=Saskatchewan, YT=Yukon)
        year: Optional year to get holidays for. Defaults to current year if not provided.

    Returns:
        Formatted string with holiday information including the next holiday

    Example:
        >>> await canadian_holidays_tool("ON")
        "Next holiday in Ontario: Family Day on 2026-02-16"

    """
    logger.info("Canadian holidays tool called for province: %s, year: %s", province_id, year)

    # Use current year if not specified
    if year is None:
        year = datetime.now(tz=UTC).year

    # Build the API URL
    url = f"https://canada-holidays.ca/api/v1/provinces/{province_id}"
    params = {"year": year}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

        logger.debug("API Response keys: %s", data.keys())

        # Extract province information - holidays are nested under province
        province_data = data.get("province", {})
        province_name = province_data.get("nameEn", province_id)
        holidays = province_data.get("holidays", [])
        next_holiday = data.get("nextHoliday")

        logger.info("Found %s holidays for %s", len(holidays), province_name)

        # Format the response
        result_parts = [f"Holidays in {province_name} for {year}:"]

        if next_holiday:
            next_date = next_holiday.get("date")
            next_name = next_holiday.get("nameEn")
            result_parts.append(f"\n🎉 Next holiday: {next_name} on {next_date}")

        if holidays:
            result_parts.append(f"\n\nAll holidays ({len(holidays)} total):")
            for holiday in holidays[:10]:  # Limit to first 10 for brevity
                date = holiday.get("date")
                name = holiday.get("nameEn")
                result_parts.append(f"  • {name}: {date}")

            if len(holidays) > 10:
                result_parts.append(f"  ... and {len(holidays) - 10} more")

        logger.debug("Successfully retrieved holidays for %s", province_id)
        return "\n".join(result_parts)

    except httpx.HTTPError as e:
        error_msg = f"Error fetching holiday data for {province_id}: {e!s}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Unexpected error: {e!s}"
        logger.error(error_msg)
        return error_msg
