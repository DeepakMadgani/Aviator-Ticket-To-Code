"""Secret Manager integration service for multiple providers."""

import base64
import json
import logging
import os
import pathlib
import re

import hvac
import requests
from google.cloud import secretmanager

logger = logging.getLogger(__name__)


class SecretsManager:
    """Service class for managing secrets retrieval from various providers."""

    def __init__(
        self,
        provider: str = "environment",
        project_id: str | None = None,
        vault_url: str | None = None,
        vault_config: str | None = None,
        kubernetes_config: str | None = None,
    ) -> None:
        """Initialize the secrets manager.

        Args:
            provider: The secrets provider ('environment', 'google', 'vault', or 'kubernetes')
            project_id: GCP project ID (required for 'google' provider)
            vault_url: Vault server URL (required for 'vault' provider)
            vault_config: JSON configuration string for Vault auth (required for 'vault' provider)
            kubernetes_config: JSON configuration string for Kubernetes API access (optional for 'kubernetes' provider)

        """
        self.provider = provider
        self.project_id = project_id
        self.vault_url = vault_url
        self.vault_config_str = vault_config
        self.kubernetes_config_str = kubernetes_config
        self._client = None
        self._vault_config = None
        self._kubernetes_config = None
        self._kubernetes_session = None

        if self.provider == "google":
            self._initialize_gcp_client()
        elif self.provider == "vault":
            self._initialize_vault_client()
        elif self.provider == "kubernetes":
            self._initialize_kubernetes_client()

    def _initialize_gcp_client(self) -> None:
        """Initialize Google Cloud Secret Manager client."""
        try:
            self._client = secretmanager.SecretManagerServiceClient()

            if not self.project_id:
                logger.warning(
                    "No project_id provided for Google Cloud Secret Manager. "
                    "Set GOOGLE_CLOUD_PROJECT environment variable."
                )
                return

            logger.info("Initialized Google Cloud Secret Manager for project: %s", self.project_id)

        except Exception as e:
            logger.error("Failed to initialize Google Cloud Secret Manager client: %s", e)
            raise

    def _raise_vault_config_error(self) -> None:
        """Raise ValueError for missing vault config."""
        logger.error("No vault_config provided for HashiCorp Vault.")
        msg = "vault_config is required for 'vault' provider"
        raise ValueError(msg)

    def _raise_vault_url_error(self) -> None:
        """Raise ValueError for missing vault URL."""
        logger.error("No vault_url provided and no 'endpoint' found in vault_config.")
        msg = "Either vault_url parameter or 'endpoint' in vault_config is required for 'vault' provider"
        raise ValueError(msg)

    def _raise_jwt_env_error(self, jwt_env_var: str) -> None:
        """Raise ValueError for missing JWT environment variable."""
        msg = f"Environment variable {jwt_env_var} not set for Kubernetes auth"
        raise ValueError(msg)

    def _raise_no_auth_method_error(self) -> None:
        """Raise ValueError for no valid authentication method."""
        msg = "No valid authentication method found in vault_config"
        raise ValueError(msg)

    def _raise_auth_failed_error(self) -> None:
        """Raise ValueError for authentication failure."""
        msg = "Vault authentication failed - client not authenticated"
        raise ValueError(msg)

    def _initialize_vault_client(self) -> None:
        """Initialize HashiCorp Vault client."""
        try:
            if not self.vault_config_str:
                self._raise_vault_config_error()

            # Parse vault configuration first
            try:
                self._vault_config = json.loads(self.vault_config_str)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in vault_config: %s", e)
                msg = "vault_config must be valid JSON"
                raise ValueError(msg) from e

            # Determine vault URL: use provided vault_url or extract from vault_config
            vault_url = self.vault_url
            if not vault_url:
                vault_url = self._vault_config.get("endpoint")
                if vault_url:
                    logger.debug("Using endpoint from vault_config as vault URL: %s", vault_url)
                else:
                    self._raise_vault_url_error()

            # Initialize hvac client
            self._client = hvac.Client(url=vault_url)

            # Authenticate with Vault
            self._authenticate_vault()

            logger.info("Initialized HashiCorp Vault client for URL: %s", vault_url)

        except Exception as e:
            logger.error("Failed to initialize HashiCorp Vault client: %s", e)
            raise

    def _authenticate_vault(self) -> None:
        """Authenticate with HashiCorp Vault using the configured method."""
        if not self._vault_config or not self._client:
            msg = "Vault client and config must be initialized before authentication"
            raise ValueError(msg)

        try:
            # Token-based authentication
            if "token" in self._vault_config:
                self._client.token = self._vault_config["token"]
                logger.debug("Using token-based authentication for Vault")

            # AppRole authentication
            elif "approle" in self._vault_config:
                approle_config = self._vault_config["approle"]
                response = self._client.auth.approle.login(
                    role_id=approle_config.get("roleId") or approle_config.get("role_id"),
                    secret_id=approle_config.get("secretId") or approle_config.get("secret_id"),
                )
                self._client.token = response["auth"]["client_token"]
                logger.debug("Successfully authenticated with Vault using AppRole")

            # Username/Password authentication
            elif "userpass" in self._vault_config:
                userpass_config = self._vault_config["userpass"]
                response = self._client.auth.userpass.login(
                    username=userpass_config["username"],
                    password=userpass_config["password"],
                )
                self._client.token = response["auth"]["client_token"]
                logger.debug("Successfully authenticated with Vault using userpass")

            # Kubernetes authentication
            elif "kubernetes" in self._vault_config:
                k8s_config = self._vault_config["kubernetes"]

                # Get app name from environment
                app_name_env_var = k8s_config.get("appNameEnvVar", "APP_NAME")
                app_name = os.getenv(app_name_env_var, "some-app")

                # Read JWT token from service account file
                jwt_env_var = k8s_config.get("jwtEnvVar", "APP_SVC_ACCT_SECRET_TOKEN")
                jwt_file_path = os.getenv(jwt_env_var)

                if not jwt_file_path:
                    self._raise_jwt_env_error(jwt_env_var)

                jwt_token = self._read_file_content(jwt_file_path)

                response = self._client.auth.kubernetes.login(
                    role=app_name,
                    jwt=jwt_token,
                )
                self._client.token = response["auth"]["client_token"]
                logger.debug("Successfully authenticated with Vault using Kubernetes")

            else:
                self._raise_no_auth_method_error()

            # Verify authentication by checking if we can access vault
            if not self._client.is_authenticated():
                self._raise_auth_failed_error()

        except Exception as e:
            logger.error("Failed to authenticate with Vault: %s", e)
            raise

    def _initialize_kubernetes_client(self) -> None:
        """Initialize Kubernetes secrets client."""
        try:
            # Parse kubernetes configuration if provided
            if self.kubernetes_config_str:
                try:
                    self._kubernetes_config = json.loads(self.kubernetes_config_str)
                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON in kubernetes_config: %s", e)
                    msg = "kubernetes_config must be valid JSON"
                    raise ValueError(msg) from e
            else:
                # Use default configuration
                self._kubernetes_config = {}

            # Set up service account paths
            self.serviceaccount_path = "/var/run/secrets/kubernetes.io/serviceaccount"

            # Read namespace - use override from config or default
            namespace_override = self._kubernetes_config.get("namespace")
            if namespace_override:
                self.namespace = namespace_override
                logger.debug("Using namespace override from config: %s", self.namespace)
            else:
                # Read from service account
                namespace_file = f"{self.serviceaccount_path}/namespace"
                if os.path.exists(namespace_file):
                    self.namespace = self._read_file_content(namespace_file).strip()
                    logger.debug("Read namespace from service account: %s", self.namespace)
                else:
                    logger.warning("Namespace file not found, using default")
                    self.namespace = "default"

            # Get API endpoint - use override from config or default
            self.api_endpoint = self._kubernetes_config.get("endpoint", "https://kubernetes.default.svc")

            # Initialize requests session with SSL verification
            self._kubernetes_session = requests.Session()

            # Set up CA certificate for SSL verification
            ca_cert_file = f"{self.serviceaccount_path}/ca.crt"
            if os.path.exists(ca_cert_file):
                self._kubernetes_session.verify = ca_cert_file
                logger.debug("Using CA certificate from service account")
            else:
                logger.warning("CA certificate file not found, SSL verification disabled")
                self._kubernetes_session.verify = False

            # Set timeout
            timeout = self._kubernetes_config.get("timeout", 60)
            self._kubernetes_session.timeout = timeout

            logger.info("Initialized Kubernetes secrets client for namespace: %s", self.namespace)

        except Exception as e:
            logger.error("Failed to initialize Kubernetes secrets client: %s", e)
            raise

    def _get_kubernetes_token(self) -> str:
        """Get the current service account token."""
        token_file = f"{self.serviceaccount_path}/token"

        if not os.path.exists(token_file):
            msg = "Service account token file not found"
            raise ValueError(msg)

        return self._read_file_content(token_file).strip()

    def _read_file_content(self, file_path: str) -> str:
        """Safely read file content with security checks.

        Args:
            file_path: Path to the file to read

        Returns:
            File content as string

        Raises:
            ValueError: If file path is invalid or insecure
            FileNotFoundError: If file doesn't exist

        """
        if not file_path:
            msg = "File path cannot be empty"
            raise ValueError(msg)

        # Security check: only allow specific patterns and directories
        if not re.match(r"^[a-zA-Z0-9.\\/\-_]+$", file_path):
            msg = "Invalid file path provided"
            raise ValueError(msg)

        # Check for path traversal attempts
        if re.search(r"^(\.\.(\/|\\|$))+", file_path):
            msg = "Path traversal not allowed"
            raise ValueError(msg)

        # Only allow reading from /var/run/secrets/ for security
        # Check both original path and resolved path to handle symlinks (common in Kubernetes)
        original_path = str(pathlib.Path(file_path))
        normalized_path = str(pathlib.Path(file_path).resolve())

        valid_original = original_path.startswith("/var/run/secrets/")
        valid_resolved = normalized_path.startswith("/var/run/secrets/")

        if not (valid_original or valid_resolved):
            msg = "File access restricted to /var/run/secrets/ directory"
            raise ValueError(msg)

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read().strip()
        except Exception as e:
            logger.error("Failed to read file %s: %s", file_path, e)
            raise
        else:
            logger.debug("Successfully read file: %s", file_path)
            return content

    def get_secret(self, secret_key: str, default_value: str | None = None) -> str | None:
        """Retrieve a secret value based on the configured provider.

        Args:
            secret_key: The secret identifier/key
            default_value: Default value if secret not found (used for environment provider)

        Returns:
            The secret value or None if not found

        Raises:
            ValueError: If provider is used but not properly configured

        """
        if self.provider == "environment":
            return os.getenv(secret_key, default_value)

        elif self.provider == "google":
            return self._get_gcp_secret(secret_key)

        elif self.provider == "vault":
            return self._get_vault_secret(secret_key)

        elif self.provider == "kubernetes":
            return self._get_kubernetes_secret(secret_key)

        else:
            logger.error("Unsupported secrets provider: %s", self.provider)
            return default_value

    def _get_gcp_secret(self, secret_key: str, version: str = "latest") -> str | None:
        """Retrieve secret from Google Cloud Secret Manager.

        Supports both simple secrets and JSON field extraction using '::' syntax:
        - 'secret-name' -> returns entire secret value
        - 'secret-name::field' -> extracts 'field' from JSON payload
        - 'secret-name::nested::field' -> extracts nested field from JSON

        Args:
            secret_key: Secret name or 'secret-name::field::path' format
            version: Version of the secret (defaults to 'latest')

        Returns:
            The secret value or extracted field, or None if not found

        """
        if not self._client:
            logger.error("Google Cloud Secret Manager client not initialized")
            return None

        if not self.project_id:
            logger.error("No project_id available for Google Cloud Secret Manager")
            return None

        if not secret_key:
            logger.error("Secret key cannot be empty")
            return None

        try:
            # Parse the secret key - split on '::' to separate secret name from field path
            secret_name, field_path = self._parse_secret_key(secret_key)

            # Build the secret version path
            secret_path = f"projects/{self.project_id}/secrets/{secret_name}/versions/{version}"

            logger.debug("Accessing secret: %s, field_path: %s", secret_name, field_path)

            # Access the secret version
            response = self._client.access_secret_version(request={"name": secret_path})

            # Extract the secret value
            secret_value = response.payload.data.decode("UTF-8")

            # If no field path specified, return the raw secret
            if not field_path:
                logger.info("Successfully retrieved secret: %s", secret_name)
                return secret_value

            # Try to parse as JSON and extract the specified field
            try:
                secret_json = json.loads(secret_value)
                extracted_value = self._extract_json_field(secret_json, field_path)

                if extracted_value is not None:
                    logger.info("Successfully retrieved secret field: %s::%s", secret_name, ":".join(field_path))
                    return str(extracted_value)
                else:
                    logger.warning(
                        "Field path '%s' not found in secret '%s'. Returning raw secret value.",
                        ":".join(field_path),
                        secret_name,
                    )
                    return secret_value

            except json.JSONDecodeError:
                logger.info(
                    "Secret '%s' is not valid JSON but field path was specified. Returning raw secret value.",
                    secret_name,
                )
                return secret_value

        except Exception as e:
            logger.error("Failed to retrieve secret '%s' from Google Cloud Secret Manager: %s", secret_key, e)
            return None

    def _parse_secret_key(self, secret_key: str) -> tuple[str, list[str]]:
        """Parse a secret key into secret name and field path.

        Args:
            secret_key: Secret key in format 'secret-name' or 'secret-name::field::nested'

        Returns:
            Tuple of (secret_name, field_path) where field_path is a list of field names

        """
        key_parts = secret_key.split("::")
        secret_name = key_parts[0]
        field_path = key_parts[1:] if len(key_parts) > 1 else []
        return secret_name, field_path

    def _extract_json_field(self, obj: dict, field_path: list) -> str | None:
        """Extract a nested field from a JSON object using a field path.

        Args:
            obj: The JSON object to extract from
            field_path: List of field names for nested access (e.g., ['user', 'password'])

        Returns:
            The extracted field value or None if not found

        """
        try:
            current = obj
            for field in field_path:
                if isinstance(current, dict) and field in current:
                    current = current[field]
                else:
                    return None
        except (KeyError, TypeError, AttributeError):
            return None
        else:
            return current

    def _get_vault_secret(self, secret_key: str) -> str | None:
        """Retrieve secret from HashiCorp Vault.

        Supports both simple secrets and JSON field extraction using '::' syntax:
        - 'secret-path' -> returns entire secret value
        - 'secret-path::field' -> extracts 'field' from JSON payload
        - 'secret-path::nested::field' -> extracts nested field from JSON

        Supports both v1 and v2 KV engines automatically based on vault_config.

        Args:
            secret_key: Secret path or 'secret-path::field::path' format

        Returns:
            The secret value or extracted field, or None if not found

        """
        if not self._client or not self._vault_config:
            logger.error("HashiCorp Vault client not initialized")
            return None

        if not secret_key:
            logger.error("Secret key cannot be empty")
            return None

        try:
            # Parse the secret key - split on '::' to separate secret path from field path
            secret_path, field_path = self._parse_secret_key(secret_key)

            # Transform path for v2 KV engine if configured
            real_path = self._get_real_vault_path(secret_path)

            logger.debug(
                "Accessing Vault secret: %s (real path: %s), field_path: %s", secret_path, real_path, field_path
            )

            try:
                # Use generic read method to avoid automatic /secret/ prepending
                # This allows us to read from custom mount points like /v1/kv1/path_name
                response = self._client.read(path=real_path)

                if not response:
                    logger.error("No response from Vault for path: %s", real_path)
                    return None

                # Handle different KV engine versions
                api_version = self._vault_config.get("apiVersion", "v1")
                if api_version == "v2":
                    # For KV v2 engines, the actual secret data is nested under response["data"]["data"]
                    # The response structure is: {"data": {"key": "value"}, "metadata": {...}}
                    secret_data = response["data"]["data"]
                    logger.debug(
                        "Retrieved secret data from KV v2 engine: %s",
                        list(secret_data.keys()) if secret_data else "No data",
                    )
                else:
                    # For KV v1 engines, data is directly in response["data"]
                    secret_data = response["data"]
                    logger.debug(
                        "Retrieved secret data from KV v1 engine: %s",
                        list(secret_data.keys()) if secret_data else "No data",
                    )

            except Exception as e:
                logger.error(
                    "Failed to read secret from Vault at path %s: %s, on get %s",
                    real_path,
                    e,
                    (self._client.url if self._client else "unknown") + "/v1/" + real_path.lstrip("/"),
                )
                return None

            # If no field path specified, return the first value if only one key, otherwise return JSON
            if not field_path:
                if len(secret_data) == 1:
                    return next(iter(secret_data.values()))
                else:
                    logger.info("Successfully retrieved secret: %s", secret_path)
                    return json.dumps(secret_data)

            # Extract the specified field
            extracted_value = self._extract_json_field(secret_data, field_path)

            if extracted_value is not None:
                logger.info("Successfully retrieved secret field: %s::%s", secret_path, ":".join(field_path))
                return str(extracted_value)
            else:
                logger.warning(
                    "Field path '%s' not found in secret '%s'",
                    "::".join(field_path),
                    secret_path,
                )
                return None

        except hvac.exceptions.Forbidden:
            logger.error("Access denied to secret '%s' - check Vault policies", secret_key)
            return None
        except hvac.exceptions.InvalidRequest as e:
            logger.error("Invalid request for secret '%s': %s", secret_key, e)
            return None
        except Exception as e:
            logger.error("Failed to retrieve secret '%s' from HashiCorp Vault: %s", secret_key, e)

            # Try to re-authenticate if token might be expired
            if "permission denied" in str(e).lower() or "invalid token" in str(e).lower():
                try:
                    logger.info("Attempting to re-authenticate with Vault")
                    self._authenticate_vault()
                    # Retry the secret retrieval once
                    return self._get_vault_secret(secret_key)
                except Exception as auth_e:
                    logger.error("Re-authentication failed: %s", auth_e)

            return None

    def _get_kubernetes_secret(self, secret_key: str) -> str | None:
        """Retrieve secret from Kubernetes secrets.

        Supports both simple secrets and nested field extraction using '::' syntax:
        - 'secret-name' -> returns entire secret value (first key if only one key exists)
        - 'secret-name::field' -> extracts 'field' from secret data
        - 'secret-name::nested::field' -> extracts nested field from JSON payload

        Args:
            secret_key: Secret name or 'secret-name::field::path' format

        Returns:
            The secret value or extracted field, or None if not found

        """
        if not self._kubernetes_session:
            logger.error("Kubernetes client not initialized")
            return None

        if not secret_key:
            logger.error("Secret key cannot be empty")
            return None

        try:
            # Parse the secret key - split on '::' to separate secret name from field path
            secret_name, field_path = self._parse_secret_key(secret_key)

            logger.debug("Accessing Kubernetes secret: %s, field_path: %s", secret_name, field_path)

            # Get the current service account token
            token = self._get_kubernetes_token()

            # Make API call to get the secret
            url = f"{self.api_endpoint}/api/v1/namespaces/{self.namespace}/secrets/{secret_name}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }

            response = self._kubernetes_session.get(url, headers=headers, timeout=self._kubernetes_session.timeout)

            if response.status_code == 200:
                secret_data = response.json()

                # Extract the data section which contains the base64-encoded secrets
                data_section = secret_data.get("data", {})

                if not data_section:
                    logger.warning("Secret '%s' exists but contains no data", secret_name)
                    return None

                # If no field path specified, get the first/only value or return JSON of all
                if not field_path:
                    if len(data_section) == 1:
                        # Single key - decode and return the value
                        encoded_value = next(iter(data_section.values()))
                        try:
                            decoded_value = base64.b64decode(encoded_value).decode("utf-8")
                        except Exception as e:
                            logger.error("Failed to decode secret value: %s", e)
                            return None
                        else:
                            logger.info("Successfully retrieved secret: %s", secret_name)
                            return decoded_value
                    else:
                        # Multiple keys - decode all and return as JSON
                        decoded_data = {}
                        for key, encoded_value in data_section.items():
                            try:
                                decoded_data[key] = base64.b64decode(encoded_value).decode("utf-8")
                            except Exception as e:
                                logger.warning("Failed to decode secret field '%s': %s", key, e)
                                decoded_data[key] = encoded_value  # Keep encoded if decode fails

                        logger.info("Successfully retrieved secret: %s", secret_name)
                        return json.dumps(decoded_data)

                # Extract specific field
                if field_path[0] not in data_section:
                    logger.warning(
                        "Field '%s' not found in secret '%s'",
                        field_path[0],
                        secret_name,
                    )
                    return None

                # Decode the base64 value for the requested field
                encoded_value = data_section[field_path[0]]
                try:
                    decoded_value = base64.b64decode(encoded_value).decode("utf-8")
                except Exception as e:
                    logger.error("Failed to decode secret field '%s': %s", field_path[0], e)
                    return None

                # If there are more field path parts, try to parse as JSON and extract nested field
                if len(field_path) > 1:
                    try:
                        secret_json = json.loads(decoded_value)
                        extracted_value = self._extract_json_field(secret_json, field_path[1:])

                        if extracted_value is not None:
                            logger.info(
                                "Successfully retrieved secret field: %s::%s", secret_name, ":".join(field_path)
                            )
                            return str(extracted_value)
                        else:
                            logger.warning(
                                "Nested field path '%s' not found in secret field '%s'",
                                ":".join(field_path[1:]),
                                field_path[0],
                            )
                            return None
                    except json.JSONDecodeError:
                        logger.info(
                            "Secret field '%s' is not valid JSON but nested path was specified. Returning raw value.",
                            field_path[0],
                        )
                        return decoded_value
                else:
                    logger.info("Successfully retrieved secret field: %s::%s", secret_name, field_path[0])
                    return decoded_value

            elif response.status_code == 404:
                logger.info("Secret '%s' not found in namespace '%s'", secret_name, self.namespace)
                return None
            elif response.status_code in [401, 403]:
                logger.error("Access denied to secret '%s' - check RBAC permissions", secret_name)

                # Try to refresh token once and retry
                try:
                    logger.info("Attempting to refresh service account token")
                    refreshed_token = self._get_kubernetes_token()
                    headers["Authorization"] = f"Bearer {refreshed_token}"
                    retry_response = self._kubernetes_session.get(
                        url, headers=headers, timeout=self._kubernetes_session.timeout
                    )

                    if retry_response.status_code == 200:
                        # Process the successful response recursively
                        return self._get_kubernetes_secret(secret_key)
                    else:
                        logger.error("Token refresh did not resolve permission issue for secret '%s'", secret_name)
                        return None
                except Exception as refresh_e:
                    logger.error("Failed to refresh token: %s", refresh_e)
                    return None
            else:
                logger.error(
                    "Failed to retrieve secret '%s': HTTP %d - %s", secret_name, response.status_code, response.text
                )
                return None

        except requests.exceptions.ConnectionError as e:
            logger.error("Failed to connect to Kubernetes API: %s", e)
            return None
        except requests.exceptions.Timeout as e:
            logger.error("Timeout calling Kubernetes API for secret '%s': %s", secret_key, e)
            return None
        except Exception as e:
            logger.error("Failed to retrieve secret '%s' from Kubernetes: %s", secret_key, e)
            return None

    def _get_real_vault_path(self, path: str) -> str:
        """Transform vault path based on pathPrefix and API version.

        Args:
            path: Original secret path

        Returns:
            Transformed path with pathPrefix applied and v2 data/ insertion if needed

        """
        if not self._vault_config:
            return path

        # Get mount point and API version from config
        path_prefix = self._vault_config.get("pathPrefix", "")
        api_version = self._vault_config.get("apiVersion", "v1")

        # Start with the original path
        transformed_path = path

        # Apply pathPrefix if specified
        if path_prefix and not path.startswith(f"{path_prefix}/"):
            # If path doesn't already start with the pathPrefix, prepend it
            transformed_path = f"{path_prefix}/{path.lstrip('/')}"

        # For v2 KV engines, also insert 'data/' after the mount point
        if api_version == "v2" and path_prefix:
            # Insert 'data/' after the mount point for v2
            # Example: kv1/myapp/config -> kv1/data/myapp/config
            mount_pattern = rf"^({re.escape(path_prefix)})\/(?!data\/)"
            transformed_path = re.sub(mount_pattern, r"\1/data/", transformed_path)

        logger.debug(
            "Path transformation: '%s' -> '%s' (pathPrefix: '%s', apiVersion: '%s')",
            path,
            transformed_path,
            path_prefix,
            api_version,
        )

        return transformed_path


def create_secrets_manager(
    provider: str,
    project_id: str | None = None,
    vault_url: str | None = None,
    vault_config: str | None = None,
    kubernetes_config: str | None = None,
) -> SecretsManager:
    """Create a SecretsManager instance.

    Args:
        provider: The secrets provider ('environment', 'google', 'vault', or 'kubernetes')
        project_id: GCP project ID (required for 'google' provider)
        vault_url: Vault server URL (required for 'vault' provider)
        vault_config: JSON configuration string for Vault auth (required for 'vault' provider)
        kubernetes_config: JSON configuration string for Kubernetes API access (optional for 'kubernetes' provider)

    Returns:
        SecretsManager instance

    """
    return SecretsManager(
        provider=provider,
        project_id=project_id,
        vault_url=vault_url,
        vault_config=vault_config,
        kubernetes_config=kubernetes_config,
    )
