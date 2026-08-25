"""DOGE Grant Savings tool - Fetches data about DOGE grant savings."""

import logging

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool(response_format="content_and_artifact")
async def doge_grant_savings() -> tuple[str, dict]:
    r"""Find data about DOGE grant savings.

    This tool retrieves information about government grant savings from the DOGE API,
    sorted by savings amount in descending order.

    Returns:
        Tuple of (content, artifact) where content is formatted text and artifact
        contains the raw API response data.

    Example:
        >>> await doge_grant_savings()
        ("Top DOGE Grant Savings:\n1. Grant ABC: $1.2M saved\n...", {...})

    """
    logger.info("DOGE grant savings tool called")

    # API endpoint
    url = "https://api.doge.gov/savings/grants"
    params = {
        "sort_by": "savings",
        "sort_order": "desc",
        "page": 1,
        "per_page": 500,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=30.0)
            logger.info("API Response Status: %s", response.status_code)
            logger.debug("API Response Headers: %s", response.headers)

            response.raise_for_status()

            # Log raw response text for debugging
            response_text = response.text
            logger.debug("Raw API response text (first 500 chars): %s", response_text[:500])

            data = response.json()

        logger.info("DOGE grant savings data retrieved successfully")
        logger.debug("Raw API response keys: %s", data.keys() if isinstance(data, dict) else "Not a dict")
        logger.debug("Response type: %s", type(data))

        # Extract grants data - API returns {'success': ..., 'result': {...}, 'meta': ...}
        grants = []
        if isinstance(data, dict):
            # Try 'result' first (DOGE API structure)
            result = data.get("result", [])

            # If result is a dict, it might have nested data
            if isinstance(result, dict):
                logger.debug("Result is a dict with keys: %s", result.keys())
                # Try common nested keys
                grants = result.get("grants", [])
                if not grants:
                    grants = result.get("data", [])
                if not grants:
                    grants = result.get("items", [])
                if not grants:
                    grants = result.get("results", [])
            elif isinstance(result, list):
                grants = result

            # Fallback to other top-level keys
            if not grants:
                grants = data.get("data", [])
            if not grants:
                grants = data.get("grants", [])
            if not grants:
                grants = data.get("results", [])
            if not grants:
                grants = data.get("items", [])
        elif isinstance(data, list):
            grants = data

        logger.info("Parsed %s grants from response", len(grants) if isinstance(grants, list) else "non-list")
        logger.debug("Grants type: %s", type(grants))

        if not grants:
            logger.warning(
                "No grants found in response. Available keys: %s",
                list(data.keys()) if isinstance(data, dict) else "N/A",
            )
            result_content = (
                "I'm sorry, but there is no DOGE grant savings data available at this time. "
                "The API returned an empty response."
            )
            return (result_content, data)

        # Ensure grants is a list
        if not isinstance(grants, list):
            logger.error("Grants is not a list, it's a %s. Value: %s", type(grants), grants)
            result_content = f"Error: Expected list of grants but got {type(grants).__name__}"
            return (result_content, data)

        # Format the response for display
        result_parts = [f"DOGE Grant Savings Data ({len(grants)} grants):"]
        result_parts.append(f"\nShowing top {min(10, len(grants))} grants by savings:\n")

        # Show top 10 grants
        for i, grant in enumerate(grants[:10], 1):
            grant_name = grant.get("name", "Unknown Grant")
            savings = grant.get("savings", 0)
            agency = grant.get("agency", "Unknown Agency")

            # Format savings amount
            if savings >= 1_000_000:
                savings_str = f"${savings / 1_000_000:.1f}M"
            elif savings >= 1_000:
                savings_str = f"${savings / 1_000:.1f}K"
            else:
                savings_str = f"${savings}"

            result_parts.append(f"{i}. {grant_name}")
            result_parts.append(f"   Agency: {agency}")
            result_parts.append(f"   Savings: {savings_str}\n")

        if len(grants) > 10:
            total_savings = sum(g.get("savings", 0) for g in grants)
            if total_savings >= 1_000_000:
                total_str = f"${total_savings / 1_000_000:.1f}M"
            else:
                total_str = f"${total_savings / 1_000:.1f}K"

            result_parts.append(f"\n... and {len(grants) - 10} more grants")
            result_parts.append(f"Total savings across all grants: {total_str}")

        result_content = "\n".join(result_parts)

        # Return tuple of (content, artifact)
        return (result_content, data)

    except httpx.HTTPError as e:
        error_msg = f"Error fetching DOGE grant savings data: {e!s}"
        logger.error(error_msg)
        return (error_msg, {"error": str(e)})
    except Exception as e:
        error_msg = f"Unexpected error: {e!s}"
        logger.error(error_msg)
        return (error_msg, {"error": str(e)})
