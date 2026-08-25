"""Tests for secrets service."""

import json
import os
from unittest.mock import Mock, patch

import pytest

from aviator.services.secrets import SecretsManager, create_secrets_manager


class TestSecretsManager:
    """Test suite for SecretsManager class."""

    def test_environment_provider_initialization(self):
        """Test initialization with environment provider."""
        manager = SecretsManager(provider="environment")
        assert manager.provider == "environment"
        assert manager._client is None

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_google_provider_initialization_success(self, mock_client_class):
        """Test successful initialization with Google provider."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        manager = SecretsManager(provider="google", project_id="test-project")
        assert manager.provider == "google"
        assert manager.project_id == "test-project"
        assert manager._client == mock_client

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_google_provider_initialization_failure(self, mock_client_class):
        """Test Google provider initialization failure."""
        mock_client_class.side_effect = RuntimeError("Failed to initialize")

        with pytest.raises(RuntimeError, match="Failed to initialize"):
            SecretsManager(provider="google", project_id="test-project")

    def test_get_secret_environment_provider(self):
        """Test getting secret from environment variables."""
        manager = SecretsManager(provider="environment")

        with patch.dict(os.environ, {"TEST_SECRET": "test_value"}):
            result = manager.get_secret("TEST_SECRET")
            assert result == "test_value"

        # Test with default value
        result = manager.get_secret("NON_EXISTENT_SECRET", "default_value")
        assert result == "default_value"

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_google_provider_success(self, mock_client_class):
        """Test successful secret retrieval from Google Cloud Secret Manager."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock the response
        mock_response = Mock()
        mock_response.payload.data = b"secret_password"
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("test-secret")

        assert result == "secret_password"
        mock_client.access_secret_version.assert_called_once_with(
            request={"name": "projects/test-project/secrets/test-secret/versions/latest"}
        )

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_google_provider_failure(self, mock_client_class):
        """Test secret retrieval failure from Google Cloud Secret Manager."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.access_secret_version.side_effect = Exception("Secret not found")

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("non-existent-secret")

        assert result is None

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_google_provider_no_client(self, mock_client_class):
        """Test Google provider with uninitialized client."""
        manager = SecretsManager(provider="google")
        manager._client = None  # Simulate uninitialized client

        result = manager.get_secret("test-secret")
        assert result is None

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_google_provider_no_project(self, mock_client_class):
        """Test Google provider without project ID."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        manager = SecretsManager(provider="google")
        manager.project_id = None  # Simulate no project ID

        result = manager.get_secret("test-secret")
        assert result is None

    def test_get_secret_unsupported_provider(self):
        """Test unsupported provider handling."""
        manager = SecretsManager(provider="unsupported")
        result = manager.get_secret("test-secret", "default")
        assert result == "default"

    def test_create_secrets_manager_factory(self):
        """Test the factory function."""
        manager = create_secrets_manager("environment")
        assert isinstance(manager, SecretsManager)
        assert manager.provider == "environment"

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_gcp_secret_with_custom_version(self, mock_client_class):
        """Test retrieving specific version of GCP secret."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_response = Mock()
        mock_response.payload.data = b"versioned_secret"
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager._get_gcp_secret("test-secret", version="2")

        assert result == "versioned_secret"
        mock_client.access_secret_version.assert_called_once_with(
            request={"name": "projects/test-project/secrets/test-secret/versions/2"}
        )

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_json_field_extraction_success(self, mock_client_class):
        """Test successful JSON field extraction using :: syntax."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock JSON secret
        json_secret = {"password": "my_password", "username": "my_user"}
        mock_response = Mock()
        mock_response.payload.data = json.dumps(json_secret).encode()
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("test-secret::password")

        assert result == "my_password"
        mock_client.access_secret_version.assert_called_once_with(
            request={"name": "projects/test-project/secrets/test-secret/versions/latest"}
        )

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_nested_json_field_extraction(self, mock_client_class):
        """Test nested JSON field extraction using multiple :: separators."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock nested JSON secret
        json_secret = {"database": {"credentials": {"password": "nested_password", "host": "localhost"}}}
        mock_response = Mock()
        mock_response.payload.data = json.dumps(json_secret).encode()
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("test-secret::database::credentials::password")

        assert result == "nested_password"

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_json_field_not_found_fallback(self, mock_client_class):
        """Test fallback when JSON field path is not found."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock JSON secret without the requested field
        json_secret = {"username": "my_user", "host": "localhost"}
        raw_secret = json.dumps(json_secret)
        mock_response = Mock()
        mock_response.payload.data = raw_secret.encode()
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("test-secret::password")  # field doesn't exist

        # Should fallback to raw secret value
        assert result == raw_secret

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_non_json_with_field_path_fallback(self, mock_client_class):
        """Test fallback when field path is specified but secret is not JSON."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock non-JSON secret
        raw_secret = "plain_text_password"
        mock_response = Mock()
        mock_response.payload.data = raw_secret.encode()
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        result = manager.get_secret("test-secret::password")  # field path but not JSON

        # Should fallback to raw secret value
        assert result == raw_secret

    @patch("aviator.services.secrets.secretmanager.SecretManagerServiceClient")
    def test_get_secret_complex_key_format(self, mock_client_class):
        """Test the complex key format from the user example."""
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        # Mock JSON secret with password field
        json_secret = {"password": "gcp_cloudsql_password", "username": "postgres", "host": "127.0.0.1", "port": 5432}
        mock_response = Mock()
        mock_response.payload.data = json.dumps(json_secret).encode()
        mock_client.access_secret_version.return_value = mock_response

        manager = SecretsManager(provider="google", project_id="test-project")
        complex_key = "gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1::password"
        result = manager.get_secret(complex_key)

        assert result == "gcp_cloudsql_password"
        mock_client.access_secret_version.assert_called_once_with(
            request={
                "name": "projects/test-project/secrets/gcp_cloudsql-otp-bs-gcp-csai-n-dev-psql-001-credentials-1/versions/latest"
            }
        )

    def test_extract_json_field_simple(self):
        """Test _extract_json_field with simple field path."""
        manager = SecretsManager(provider="google")

        obj = {"password": "test_password", "username": "test_user"}
        result = manager._extract_json_field(obj, ["password"])
        assert result == "test_password"

        result = manager._extract_json_field(obj, ["username"])
        assert result == "test_user"

        # Field not found
        result = manager._extract_json_field(obj, ["not_found"])
        assert result is None

    def test_extract_json_field_nested(self):
        """Test _extract_json_field with nested field paths."""
        manager = SecretsManager(provider="google")

        obj = {
            "database": {"credentials": {"password": "nested_password", "host": "localhost"}, "config": {"port": 5432}}
        }

        result = manager._extract_json_field(obj, ["database", "credentials", "password"])
        assert result == "nested_password"

        result = manager._extract_json_field(obj, ["database", "config", "port"])
        assert result == 5432

        # Path not found
        result = manager._extract_json_field(obj, ["database", "credentials", "not_found"])
        assert result is None

        result = manager._extract_json_field(obj, ["not_found", "field"])
        assert result is None

    def test_extract_json_field_edge_cases(self):
        """Test _extract_json_field with edge cases."""
        manager = SecretsManager(provider="google")

        # Empty field path
        obj = {"password": "test_password"}
        result = manager._extract_json_field(obj, [])
        assert result == obj

        # Non-dict object in path
        obj = {"field": "string_value"}
        result = manager._extract_json_field(obj, ["field", "nested"])
        assert result is None

        # None object
        result = manager._extract_json_field(None, ["field"])
        assert result is None
