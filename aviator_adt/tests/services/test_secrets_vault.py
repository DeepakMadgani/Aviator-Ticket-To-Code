"""Tests for Vault secrets provider functionality."""

import json
from unittest.mock import Mock, mock_open, patch

import pytest

from aviator.services.secrets import SecretsManager, create_secrets_manager


class TestVaultSecretsManager:
    """Test suite for Vault secrets provider functionality."""

    def test_vault_provider_initialization_success(self):
        """Test successful initialization with Vault provider."""
        vault_config = json.dumps({"token": "test-token", "apiVersion": "v2", "pathPrefix": "secret"})

        with patch("hvac.Client") as mock_hvac_client:
            mock_client = Mock()
            mock_hvac_client.return_value = mock_client
            mock_client.is_authenticated.return_value = True

            manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

            assert manager.provider == "vault"
            assert manager.vault_url == "https://vault.example.com"
            assert manager._vault_config == json.loads(vault_config)
            assert manager._client == mock_client

    def test_vault_provider_initialization_no_url_and_no_endpoint(self):
        """Test Vault provider initialization failure due to missing URL and endpoint."""
        vault_config = json.dumps({"token": "test-token"})

        with pytest.raises(ValueError, match="Either vault_url parameter or 'endpoint' in vault_config is required"):
            SecretsManager(provider="vault", vault_url=None, vault_config=vault_config)

    @patch("hvac.Client")
    def test_vault_provider_initialization_with_endpoint_in_config(self, mock_hvac_client):
        """Test successful initialization using endpoint from vault_config."""
        vault_config = json.dumps({"token": "test-token", "endpoint": "https://vault.config-endpoint.com"})

        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True

        manager = SecretsManager(
            provider="vault",
            vault_url=None,  # No vault_url provided
            vault_config=vault_config,
        )

        assert manager.provider == "vault"
        assert manager.vault_url is None  # vault_url parameter was None
        assert manager._vault_config["endpoint"] == "https://vault.config-endpoint.com"
        assert manager._client == mock_client
        # Verify hvac.Client was called with the endpoint URL from config
        mock_hvac_client.assert_called_once_with(url="https://vault.config-endpoint.com")

    def test_vault_provider_initialization_no_config(self):
        """Test Vault provider initialization failure due to missing config."""
        with pytest.raises(ValueError, match="vault_config is required"):
            SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=None)

    def test_vault_provider_initialization_invalid_json(self):
        """Test Vault provider initialization failure due to invalid JSON config."""
        with pytest.raises(ValueError, match="vault_config must be valid JSON"):
            SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config="invalid-json")

    @patch("hvac.Client")
    def test_vault_token_authentication(self, mock_hvac_client):
        """Test Vault token-based authentication."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True

        vault_config = json.dumps({"token": "test-token"})

        SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        assert mock_client.token == "test-token"

    @patch("hvac.Client")
    def test_vault_approle_authentication(self, mock_hvac_client):
        """Test Vault AppRole authentication."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.auth.approle.login.return_value = {"auth": {"client_token": "authenticated-token"}}

        vault_config = json.dumps({"approle": {"roleId": "test-role-id", "secretId": "test-secret-id"}})

        SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        mock_client.auth.approle.login.assert_called_once_with(role_id="test-role-id", secret_id="test-secret-id")
        assert mock_client.token == "authenticated-token"

    @patch("hvac.Client")
    def test_vault_userpass_authentication(self, mock_hvac_client):
        """Test Vault username/password authentication."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.auth.userpass.login.return_value = {"auth": {"client_token": "authenticated-token"}}

        vault_config = json.dumps({"userpass": {"username": "test-user", "password": "test-password"}})

        SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        mock_client.auth.userpass.login.assert_called_once_with(username="test-user", password="test-password")
        assert mock_client.token == "authenticated-token"

    @patch("os.getenv")
    @patch("builtins.open", new_callable=mock_open, read_data="jwt-token-content")
    @patch("pathlib.Path")
    @patch("hvac.Client")
    def test_vault_kubernetes_authentication(self, mock_hvac_client, mock_path, mock_file, mock_getenv):
        """Test Vault Kubernetes authentication."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.auth.kubernetes.login.return_value = {"auth": {"client_token": "authenticated-token"}}

        # Mock pathlib.Path to return a path that passes security check
        mock_path_instance = Mock()
        mock_path_instance.resolve.return_value = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        mock_path.return_value = mock_path_instance

        # Mock environment variables
        mock_getenv.side_effect = lambda key, default=None: {
            "APP_NAME": "test-app",
            "APP_SVC_ACCT_SECRET_TOKEN": "/var/run/secrets/kubernetes.io/serviceaccount/token",
        }.get(key, default)

        vault_config = json.dumps(
            {"kubernetes": {"appNameEnvVar": "APP_NAME", "jwtEnvVar": "APP_SVC_ACCT_SECRET_TOKEN"}}
        )

        SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        mock_client.auth.kubernetes.login.assert_called_once_with(role="test-app", jwt="jwt-token-content")
        assert mock_client.token == "authenticated-token"

    @patch("hvac.Client")
    def test_vault_get_secret_simple_v2(self, mock_hvac_client):
        """Test getting a simple secret from Vault KV v2."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.url = "https://vault.example.com"  # Add url attribute for error logging

        # Mock Vault read response for v2 (data is nested under "data")
        mock_client.read.return_value = {"data": {"data": {"password": "secret-value"}}}

        vault_config = json.dumps({"token": "test-token", "apiVersion": "v2", "pathPrefix": "secret"})

        manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        result = manager.get_secret("myapp/config::password")
        assert result == "secret-value"

    @patch("hvac.Client")
    def test_vault_get_secret_simple_v1(self, mock_hvac_client):
        """Test getting a secret from Vault KV v1."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.url = "https://vault.example.com"  # Add url attribute for error logging

        # Mock Vault read response for v1
        mock_client.read.return_value = {"data": {"password": "secret-value"}}

        vault_config = json.dumps({"token": "test-token"})

        manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        result = manager.get_secret("myapp/config::password")
        assert result == "secret-value"

    @patch("hvac.Client")
    def test_vault_get_secret_json_return_for_multiple_keys(self, mock_hvac_client):
        """Test that multiple keys return JSON string."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.url = "https://vault.example.com"  # Add url attribute for error logging

        mock_client.read.return_value = {"data": {"username": "admin", "password": "secret", "port": "5432"}}

        vault_config = json.dumps({"token": "test-token"})

        manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        result = manager.get_secret("myapp/config")
        result_dict = json.loads(result)
        assert result_dict["username"] == "admin"
        assert result_dict["password"] == "secret"
        assert result_dict["port"] == "5432"

    @patch("hvac.Client")
    def test_vault_get_secret_single_key_return_value(self, mock_hvac_client):
        """Test that single key returns the value directly."""
        mock_client = Mock()
        mock_hvac_client.return_value = mock_client
        mock_client.is_authenticated.return_value = True
        mock_client.url = "https://vault.example.com"  # Add url attribute for error logging

        mock_client.read.return_value = {"data": {"password": "secret-value"}}

        vault_config = json.dumps({"token": "test-token"})

        manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

        result = manager.get_secret("myapp/config")
        assert result == "secret-value"

    def test_create_secrets_manager_vault_factory(self):
        """Test the factory function with vault provider."""
        with patch("hvac.Client") as mock_hvac_client:
            mock_client = Mock()
            mock_hvac_client.return_value = mock_client
            mock_client.is_authenticated.return_value = True

            vault_config = json.dumps({"token": "test-token"})

            manager = create_secrets_manager(
                provider="vault", vault_url="https://vault.example.com", vault_config=vault_config
            )

            assert isinstance(manager, SecretsManager)
            assert manager.provider == "vault"
            assert manager.vault_url == "https://vault.example.com"

    def test_vault_path_transformation_v2(self):
        """Test path transformation for KV v2 engine."""
        vault_config = json.dumps({"token": "test-token", "apiVersion": "v2", "pathPrefix": "secret"})

        with patch("hvac.Client") as mock_hvac_client:
            mock_client = Mock()
            mock_hvac_client.return_value = mock_client
            mock_client.is_authenticated.return_value = True

            manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

            # Test path transformation
            transformed_path = manager._get_real_vault_path("secret/myapp/config")
            assert transformed_path == "secret/data/myapp/config"

            # Test path already has data
            transformed_path = manager._get_real_vault_path("secret/data/myapp/config")
            assert transformed_path == "secret/data/myapp/config"

    def test_vault_path_transformation_v1(self):
        """Test no path transformation for KV v1 engine."""
        vault_config = json.dumps({"token": "test-token", "apiVersion": "v1", "pathPrefix": "secret"})

        with patch("hvac.Client") as mock_hvac_client:
            mock_client = Mock()
            mock_hvac_client.return_value = mock_client
            mock_client.is_authenticated.return_value = True

            manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

            # Test no transformation for v1
            original_path = "secret/myapp/config"
            transformed_path = manager._get_real_vault_path(original_path)
            assert transformed_path == original_path

    def test_read_file_content_security_checks(self):
        """Test file reading security validations."""
        vault_config = json.dumps({"token": "test-token"})

        with patch("hvac.Client") as mock_hvac_client:
            mock_client = Mock()
            mock_hvac_client.return_value = mock_client
            mock_client.is_authenticated.return_value = True

            manager = SecretsManager(provider="vault", vault_url="https://vault.example.com", vault_config=vault_config)

            # Test empty file path
            with pytest.raises(ValueError, match="File path cannot be empty"):
                manager._read_file_content("")

            # Test invalid characters
            with pytest.raises(ValueError, match="Invalid file path provided"):
                manager._read_file_content("file;rm -rf /")

            # Test path traversal
            with pytest.raises(ValueError, match="Path traversal not allowed"):
                manager._read_file_content("../../../etc/passwd")

            # Test restricted directory - mock pathlib.Path to return resolved path outside allowed directory
            with patch("pathlib.Path") as mock_path:
                mock_path_instance = Mock()
                mock_path_instance.resolve.return_value = "/etc/passwd"  # Not in /var/run/secrets/
                mock_path.return_value = mock_path_instance

                with pytest.raises(ValueError, match="File access restricted"):
                    manager._read_file_content("/etc/passwd")
