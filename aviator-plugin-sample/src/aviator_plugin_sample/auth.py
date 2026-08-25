"""OAuth 2.0 authentication example for Aviator plugin.

This module demonstrates how to implement OAuth authentication for your plugin.
This is a sample implementation showing the pattern - adapt to your OAuth provider.
"""

import logging
from typing import Annotated

from aviator.models import Chunk
from fastapi import Depends, HTTPException, Security, WebSocket, WebSocketException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# OAuth2 configuration

# Token URL for getting access tokens (Client Credentials grant)
OAUTH2_TOKEN_URL_BROWSER = "http://localhost:8080/otdsws/oauth2/token"  # Browser-accessible
OAUTH2_TOKEN_URL_BACKEND = "http://otds_server:8080/otdsws/oauth2/token"  # Container network
CLIENT_ID = "plugin"
CLIENT_SECRET = "W03nBTcA0g0zU1h9Cg6xoB8bJwrV0t2Y"


# Bearer token authentication scheme for Swagger UI
oauth2_scheme = HTTPBearer(
    scheme_name="Bearer Token",
    description="Get token from OTDS: POST http://localhost:8080/otdsws/oauth2/token (grant_type=client_credentials)",
    auto_error=False,
)


class TokenData(BaseModel):
    """Token data model."""

    username: str | None = None
    scopes: list[str] = []


class User(BaseModel):
    """User model."""

    username: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool = False
    scopes: list[str] = []


def verify_token(token: str) -> TokenData:
    """Verify OAuth token.

    For OTDS client_credentials tokens, we accept any non-empty bearer token.
    In production, you should validate the JWT signature or call a validation endpoint.

    Args:
        token: OAuth2 access token from the provider

    Returns:
        TokenData object with decoded information

    Raises:
        HTTPException: If token is invalid or validation fails

    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    def _raise_credentials_exception() -> None:
        raise credentials_exception

    try:
        # Basic validation: token must be non-empty
        if not token or len(token) < 10:
            logger.warning("Token is empty or too short")
            _raise_credentials_exception()

        # For client_credentials grant, use the client_id as username
        logger.info("Token accepted (length: %s)", len(token))

        # Use a default username for client credentials
        username = CLIENT_ID
        scopes = []

        token_data = TokenData(username=username, scopes=scopes)
        return token_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error validating token: %s", e)
        raise credentials_exception from e


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(oauth2_scheme)],
) -> User:
    """Get the current authenticated user from the OAuth token.

    This function:
    1. Validates the OAuth token
    2. Extracts user information
    3. Returns the user object

    In a real implementation, you would:
    - Call your OAuth provider's user info endpoint
    - Validate scopes and permissions
    - Cache user information

    Args:
        credentials: HTTP Bearer credentials with access token (optional)

    Returns:
        User object with user information (anonymous if no token)

    Raises:
        HTTPException: If authentication fails

    """
    # Allow anonymous access if no credentials provided
    if credentials is None:
        return User(
            username="anonymous",
            email="anonymous@example.com",
            full_name="Anonymous User",
            disabled=False,
        )

    token = credentials.credentials

    if not token:
        # Return anonymous user instead of raising error
        return User(
            username="anonymous",
            email="anonymous@example.com",
            full_name="Anonymous User",
            disabled=False,
        )

    def _raise_inactive_user_exception() -> None:
        raise HTTPException(status_code=400, detail="Inactive user")

    try:
        # Verify the token with OAuth provider
        token_data = await verify_token(token)

        user = User(
            username=token_data.username,
            email=f"{token_data.username}@example.com",
            full_name=f"{token_data.username.title()} User",
            disabled=False,
            scopes=token_data.scopes,
        )

        if user.disabled:
            _raise_inactive_user_exception()

        logger.info("User authenticated: %s", user.username)
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Authentication error: %s", e)
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def sample_permission_filter(chunks: list[Chunk], user: dict) -> list[Chunk]:
    """Filter RAG chunks based on user permissions from OAuth authentication.

    This function filters document chunks based on the authenticated user's permissions.
    It integrates with the OAuth authentication to enforce document-level access control.

    Args:
        chunks (list[Chunk]): List of RAG chunks to filter based on permissions.
        user (dict): User information dictionary from get_auth_plugin containing:
            - username: User's username
            - email: User's email address
            - scopes: List of OAuth scopes/permissions
            - full_name: User's display name

    Returns:
        list[Chunk]: Filtered list of chunks the user has permission to access.
            Returns empty list if user lacks valid credentials.

    """
    logger.info("sample_permission_filter: Filtering chunks for user: %s", user.get("username", "unknown"))
    # Return empty list if no chunks provided
    if not chunks:
        logger.debug("sample_permission_filter: No chunks to filter.")
        return []

    # Return empty list if user is not authenticated
    if not user:
        logger.warning("sample_permission_filter: No user provided, denying access to all chunks.")
        return []

    username = user.get("username", "unknown")
    scopes = user.get("scopes", [])

    logger.debug(
        "sample_permission_filter: Filtering %d chunks for user '%s' with scopes: %s",
        len(chunks),
        username,
        scopes,
    )

    # Extract unique document IDs from chunks for permission checking
    document_ids = {chunk.document_id for chunk in chunks}
    logger.debug("sample_permission_filter: Found %d unique documents", len(document_ids))

    # Check if user has required scopes for RAG access
    # You can customize these permission rules based on your requirements:
    # - Allow all authenticated users
    # - Check specific scopes (e.g., "read", "documents:read")
    # - Implement custom permission logic

    # Example 1: Allow all authenticated users with any valid username
    if username and username != "unknown":
        logger.info(
            "sample_permission_filter: User '%s' has access, returning %d chunks",
            username,
            len(chunks),
        )
        return chunks

    # Example 2: Check for specific scopes (uncomment to enable)
    # if "read" in scopes or "documents:read" in scopes:
    #     logger.info(
    #         "sample_permission_filter: User '%s' has required scopes, returning %d chunks",
    #         username,
    #         len(chunks),
    #     )
    #     return chunks

    # Example 3: Filter by document permissions (implement your logic)
    # filtered_chunks = []
    # for chunk in chunks:
    #     doc_id = chunk.metadata.get("source", "")
    #     if has_permission(username, doc_id):  # Implement has_permission function
    #         filtered_chunks.append(chunk)
    # return filtered_chunks

    # Default: deny access if no conditions matched
    logger.warning(
        "sample_permission_filter: User '%s' does not meet access criteria, filtering all chunks.",
        username,
    )
    return []


async def get_current_active_user(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Get the current active user.

    Additional check to ensure user is active.

    Args:
        current_user: Current user from get_current_user

    Returns:
        Active user object

    Raises:
        HTTPException: If user is inactive

    """
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


# Authentication plugin for Aviator
def get_auth_plugin(credentials: Annotated[HTTPAuthorizationCredentials | None, Security(oauth2_scheme)]) -> dict:
    """Authenticate user via OAuth2 token.

    This is the main auth handler called by Aviator for each request.

    Args:
        credentials: HTTP Bearer credentials with access token (optional)

    Returns:
        Dictionary with user information (anonymous if no token)

    Raises:
        HTTPException: If authentication fails

    """
    logger.info("OAuth authentication invoked")

    # --- AUTH ENFORCEMENT BLOCK ---
    # To require authentication, uncomment the following lines:
    # if credentials is None or not getattr(credentials, "credentials", None):
    #     logger.info("No credentials or empty token provided, rejecting request")
    #     raise HTTPException(
    #         status_code=401,
    #         detail="Not authenticated",
    #         headers={"WWW-Authenticate": "Bearer"},
    #     )
    # --- END AUTH ENFORCEMENT ---
    token = credentials.credentials if credentials is not None else None

    if not token:
        # Return anonymous user instead of raising error
        logger.info("Empty token or no credentials, using anonymous user")
        return {
            "username": "anonymous",
            "email": "anonymous@example.com",
            "full_name": "Anonymous User",
            "scopes": [],
        }

    try:
        # Verify the token with OAuth provider
        token_data = verify_token(token)

        # Return user information as dict
        user_info = {
            "username": token_data.username,
            "email": f"{token_data.username}@example.com",
            "full_name": f"{token_data.username.title()} User",
            "scopes": token_data.scopes,
        }

        logger.info("User authenticated: %s", token_data.username)
        return user_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Authentication error: %s", e)
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def get_auth_plugin_ws(user: dict | None = None, websocket: WebSocket | None = None) -> dict:
    """Authenticate WebSocket connection.

    This handler is called when a WebSocket connection is established.

    Args:
        user: Optional pre-authenticated user information
        websocket: Optional WebSocket connection object

    Returns:
        dict: User information dictionary

    Raises:
        WebSocketException: If authentication fails

    """
    logger.info("WebSocket authentication invoked")

    # If user is already authenticated, return it
    if user:
        logger.info("Using pre-authenticated user: %s", user.get("username", "unknown"))
        return user

    # If no websocket provided, return anonymous user
    if not websocket:
        logger.info("No websocket provided, returning anonymous user")
        return {"username": "anonymous", "authenticated": False}

    # Extract authentication from query parameters, headers, or cookies
    token = websocket.query_params.get("token")

    if not token:
        # Check headers as fallback
        token = websocket.headers.get("authorization")
        if token and token.startswith("Bearer "):
            token = token[7:]

    if not token:
        logger.warning("No authentication token provided for WebSocket, using anonymous")
        return {"username": "anonymous", "authenticated": False}

    try:
        # Verify the token (example - adjust based on your auth system)
        # user_info = await verify_token(token)
        user_info = {"username": "example_user", "authenticated": True}

        logger.info("WebSocket authenticated for user: %s", user_info["username"])
        return user_info

    except Exception as e:
        logger.error("WebSocket authentication failed: %s", e)
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid authentication token") from e
