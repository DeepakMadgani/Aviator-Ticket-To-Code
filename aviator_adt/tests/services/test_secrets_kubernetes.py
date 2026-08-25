"""Tests for Kubernetes secrets provider functionality."""

import base64
import json
from unittest.mock import Mock, patch

import pytest
import requests

from aviator.services.secrets import SecretsManager, create_secrets_manager


class TestKubernetesSecretsManager:
    """Test suite for Kubernetes secrets provider functionality."""

    def test_kubernetes_provider_initialization_success(self):
        """Test successful initialization with Kubernetes provider."""
        kubernetes_config = json.dumps({"endpoint": "https://k8s.example.com", "timeout": 30})

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session") as mock_session,
        ):
            mock_exists.side_effect = lambda path: path.endswith(("namespace", "ca.crt"))
            mock_read_file.return_value = "test-namespace"
            mock_session_instance = Mock()
            mock_session.return_value = mock_session_instance

            manager = SecretsManager(provider="kubernetes", kubernetes_config=kubernetes_config)

            assert manager.provider == "kubernetes"
            assert manager._kubernetes_config == json.loads(kubernetes_config)
            assert manager.namespace == "test-namespace"
            assert manager.api_endpoint == "https://k8s.example.com"
            assert manager._kubernetes_session == mock_session_instance

    def test_kubernetes_provider_initialization_default_config(self):
        """Test initialization with default Kubernetes configuration."""
        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session") as mock_session,
        ):
            mock_exists.side_effect = lambda path: path.endswith(("namespace", "ca.crt"))
            mock_read_file.return_value = "default-namespace"
            mock_session_instance = Mock()
            mock_session.return_value = mock_session_instance

            manager = SecretsManager(provider="kubernetes")

            assert manager.provider == "kubernetes"
            assert manager._kubernetes_config == {}
            assert manager.namespace == "default-namespace"
            assert manager.api_endpoint == "https://kubernetes.default.svc"
            assert manager._kubernetes_session == mock_session_instance

    def test_kubernetes_provider_initialization_namespace_override(self):
        """Test initialization with namespace override in config."""
        kubernetes_config = json.dumps({"namespace": "custom-namespace"})

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session") as mock_session,
        ):
            mock_exists.side_effect = lambda path: path.endswith("ca.crt")
            mock_session_instance = Mock()
            mock_session.return_value = mock_session_instance

            manager = SecretsManager(provider="kubernetes", kubernetes_config=kubernetes_config)

            assert manager.namespace == "custom-namespace"
            # Should not call _read_file_content for namespace since it's overridden
            mock_read_file.assert_not_called()

    def test_kubernetes_provider_initialization_missing_namespace_file(self):
        """Test initialization when namespace file is missing."""
        with patch("os.path.exists") as mock_exists, patch("requests.Session") as mock_session:
            mock_exists.return_value = False
            mock_session_instance = Mock()
            mock_session.return_value = mock_session_instance

            manager = SecretsManager(provider="kubernetes")

            assert manager.namespace == "default"

    def test_kubernetes_provider_initialization_invalid_json(self):
        """Test Kubernetes provider initialization failure due to invalid JSON config."""
        with pytest.raises(ValueError, match="kubernetes_config must be valid JSON"):
            SecretsManager(provider="kubernetes", kubernetes_config="invalid-json")

    def test_kubernetes_provider_initialization_failure(self):
        """Test Kubernetes provider initialization failure."""
        with (
            patch("requests.Session", side_effect=Exception("Session creation failed")),
            pytest.raises(Exception, match="Session creation failed"),
        ):
            SecretsManager(provider="kubernetes")

    def test_get_kubernetes_token_success(self):
        """Test successful token retrieval."""
        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session"),
        ):
            mock_exists.return_value = True
            mock_read_file.side_effect = ["test-namespace", "test-token"]

            manager = SecretsManager(provider="kubernetes")
            token = manager._get_kubernetes_token()

            assert token == "test-token"

    def test_get_kubernetes_token_file_not_found(self):
        """Test token retrieval when token file doesn't exist."""
        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session"),
        ):
            # Setup mocks: namespace file exists and has content, token file doesn't exist
            mock_exists.side_effect = lambda path: path.endswith("namespace") and not path.endswith("token")
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")

            with pytest.raises(ValueError, match="Service account token file not found"):
                manager._get_kubernetes_token()

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_success_single_field(self, mock_session_class, mock_get_token):
        """Test successful secret retrieval with single field."""
        # Mock session and response
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock Kubernetes API response
        secret_data = {"password": base64.b64encode(b"test-password").decode()}
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        # Mock token
        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            assert result == "test-password"
            mock_session.get.assert_called_once()
            call_args = mock_session.get.call_args
            assert "/api/v1/namespaces/test-namespace/secrets/test-secret" in call_args[0][0]

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_success_multiple_fields(self, mock_session_class, mock_get_token):
        """Test successful secret retrieval with multiple fields (returns JSON)."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock Kubernetes API response with multiple fields
        secret_data = {
            "username": base64.b64encode(b"test-user").decode(),
            "password": base64.b64encode(b"test-password").decode(),
        }
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            # Should return JSON with decoded values
            result_data = json.loads(result)
            assert result_data["username"] == "test-user"
            assert result_data["password"] == "test-password"

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_field_extraction(self, mock_session_class, mock_get_token):
        """Test secret retrieval with field extraction using :: syntax."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Mock Kubernetes API response
        secret_data = {
            "config": base64.b64encode(b'{"database": {"password": "db-pass"}, "api_key": "api-123"}').decode()
        }
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")

            # Test field extraction
            result = manager.get_secret("test-secret::config")
            expected_config = '{"database": {"password": "db-pass"}, "api_key": "api-123"}'
            assert result == expected_config

            # Test nested field extraction
            result = manager.get_secret("test-secret::config::database::password")
            assert result == "db-pass"

            # Test simple field extraction
            secret_data = {"password": base64.b64encode(b"simple-pass").decode()}
            mock_response.json.return_value = {"data": secret_data}
            result = manager.get_secret("test-secret::password")
            assert result == "simple-pass"

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_not_found(self, mock_session_class, mock_get_token):
        """Test secret retrieval when secret doesn't exist."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 404
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("non-existent-secret")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_permission_denied(self, mock_session_class, mock_get_token):
        """Test secret retrieval with permission denied (403)."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 403
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("restricted-secret")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_permission_denied_with_retry_success(self, mock_session_class, mock_get_token):
        """Test secret retrieval with permission denied that succeeds on token refresh."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # First response: 403, second response: 200, third response (for recursive call): 200
        secret_data = {"password": base64.b64encode(b"test-password").decode()}
        mock_response_403 = Mock()
        mock_response_403.status_code = 403
        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {"data": secret_data}
        mock_response_200_recursive = Mock()
        mock_response_200_recursive.status_code = 200
        mock_response_200_recursive.json.return_value = {"data": secret_data}

        # The retry logic makes a recursive call, so we need three responses:
        # 1. Initial 403
        # 2. Retry attempt (still within the same call) - 200
        # 3. Recursive call - 200
        mock_session.get.side_effect = [mock_response_403, mock_response_200, mock_response_200_recursive]

        mock_get_token.return_value = "refreshed-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            assert result == "test-password"
            # Should make 3 calls: initial (403), retry (200), and recursive (200)
            assert mock_session.get.call_count == 3

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_connection_error(self, mock_session_class, mock_get_token):
        """Test secret retrieval with connection error."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection failed")
        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_timeout(self, mock_session_class, mock_get_token):
        """Test secret retrieval with timeout."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_session.get.side_effect = requests.exceptions.Timeout("Request timed out")
        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_empty_data(self, mock_session_class, mock_get_token):
        """Test secret retrieval when secret exists but has no data."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {}}  # Empty data
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("empty-secret")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_field_not_found(self, mock_session_class, mock_get_token):
        """Test secret retrieval when requested field doesn't exist."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        secret_data = {"password": base64.b64encode(b"test-password").decode()}
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret::nonexistent_field")

            assert result is None

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_base64_decode_error(self, mock_session_class, mock_get_token):
        """Test secret retrieval with base64 decode error."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Invalid base64 data
        secret_data = {"password": "invalid-base64!@#$"}
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret::password")

            assert result is None

    def test_get_secret_kubernetes_no_session(self):
        """Test secret retrieval when Kubernetes session is not initialized."""
        manager = SecretsManager(provider="environment")  # Initialize with different provider
        manager.provider = "kubernetes"  # Change provider but don't initialize session
        manager._kubernetes_session = None

        result = manager.get_secret("test-secret")
        assert result is None

    def test_get_secret_kubernetes_empty_key(self):
        """Test secret retrieval with empty secret key."""
        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session"),
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("")

            assert result is None

    def test_parse_secret_key_helper(self):
        """Test the _parse_secret_key helper function."""
        manager = SecretsManager(provider="environment")

        # Test simple secret name
        secret_name, field_path = manager._parse_secret_key("simple-secret")
        assert secret_name == "simple-secret"
        assert field_path == []

        # Test with field path
        secret_name, field_path = manager._parse_secret_key("secret::field")
        assert secret_name == "secret"
        assert field_path == ["field"]

        # Test with nested field path
        secret_name, field_path = manager._parse_secret_key("secret::config::database::password")
        assert secret_name == "secret"
        assert field_path == ["config", "database", "password"]

    def test_create_secrets_manager_kubernetes(self):
        """Test creating Kubernetes secrets manager via factory."""
        kubernetes_config = json.dumps({"endpoint": "https://k8s.test.com"})

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
            patch("requests.Session"),
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = create_secrets_manager(provider="kubernetes", kubernetes_config=kubernetes_config)

            assert isinstance(manager, SecretsManager)
            assert manager.provider == "kubernetes"
            assert manager._kubernetes_config["endpoint"] == "https://k8s.test.com"

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_non_json_nested_field(self, mock_session_class, mock_get_token):
        """Test secret retrieval when trying to extract nested field from non-JSON data."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        # Non-JSON data in the secret
        secret_data = {"config": base64.b64encode(b"plain-text-config").decode()}
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": secret_data}
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")

            # Try to extract nested field from non-JSON - should return the raw value
            result = manager.get_secret("test-secret::config::nested_field")
            assert result == "plain-text-config"

    @patch("aviator.services.secrets.SecretsManager._get_kubernetes_token")
    @patch("requests.Session")
    def test_get_secret_kubernetes_http_error(self, mock_session_class, mock_get_token):
        """Test secret retrieval with HTTP error (non-403/404)."""
        mock_session = Mock()
        mock_session_class.return_value = mock_session

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_session.get.return_value = mock_response

        mock_get_token.return_value = "test-token"

        with (
            patch("aviator.services.secrets.SecretsManager._read_file_content") as mock_read_file,
            patch("os.path.exists") as mock_exists,
        ):
            mock_exists.return_value = True
            mock_read_file.return_value = "test-namespace"

            manager = SecretsManager(provider="kubernetes")
            result = manager.get_secret("test-secret")

            assert result is None

            assert result is None
