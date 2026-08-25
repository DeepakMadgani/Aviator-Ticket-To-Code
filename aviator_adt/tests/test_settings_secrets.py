"""Tests for settings secrets manager integration."""

import math
import os
from unittest.mock import Mock, patch

import pytest

from aviator.settings import Settings
from aviator.settings_secrets import (
    get_anthropic_api_key,
    get_aws_bedrock_access_key,
    get_aws_bedrock_secret_key,
    get_azure_openai_api_key,
    get_broker_password,
    get_mistral_api_key,
    get_openai_api_key,
    get_postgres_password,
)


class TestSettingsSecretsIntegration:
    """Test suite for Settings class secrets manager integration."""

    def test_rag_list_query_limit_computed_field(self):
        """Test rag_list_query_limit is derived from token budget and chunk size."""
        settings = Settings(total_token_size=20000, text_splitter_chunk_size=1000)

        expected = math.ceil(20000 * 0.70 / (1000 // 4))
        assert settings.rag_list_query_limit == expected

    def test_postgres_password_environment_provider_default(self):
        """Test postgres password retrieval with default environment provider."""
        # Explicitly clear POSTGRES_PASSWORD in case .env loaded it
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(postgres_password=None)

        # Should default to environment provider
        assert settings.secrets_manager == "environment"

        # Test error when no password is set
        with pytest.raises(ValueError, match="POSTGRES_PASSWORD environment variable not set"):
            get_postgres_password(settings)

    def test_postgres_password_environment_provider_with_env_var(self):
        """Test postgres password retrieval from environment variable."""
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "env_password"}):
            settings = Settings(postgres_password="env_password")
            password = get_postgres_password(settings)
            assert password == "env_password"

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_google_provider_success(self, mock_create_manager):
        """Test successful postgres password retrieval from Google Secret Manager."""
        # Mock the secrets manager
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "gcp_secret_password"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key="postgres-password",
            google_cloud_project="test-project",
        )

        password = get_postgres_password(settings)

        assert password == "gcp_secret_password"
        mock_create_manager.assert_called_with(provider="google", project_id="test-project")
        mock_manager.get_secret.assert_called_with("postgres-password")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_google_provider_failure(self, mock_create_manager):
        """Test postgres password failure when Google Secret Manager returns None."""
        # Mock the secrets manager to return None (failure)
        mock_manager = Mock()
        mock_manager.get_secret.return_value = None
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key="postgres-password",
            postgres_password="fallback_password",
        )

        with pytest.raises(ValueError, match="Failed to retrieve postgres password from Google Cloud Secret Manager"):
            get_postgres_password(settings)

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_google_provider_exception(self, mock_create_manager):
        """Test postgres password failure when Google Secret Manager raises exception."""
        # Mock the secrets manager to raise an exception
        mock_create_manager.side_effect = Exception("GCP error")

        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key="postgres-password",
            postgres_password="fallback_password",
        )

        with pytest.raises(ValueError, match="Error retrieving postgres password from Google Cloud Secret Manager"):
            get_postgres_password(settings)

    def test_postgres_password_google_provider_no_key(self):
        """Test postgres password failure when Google provider is set but no key is provided."""
        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key=None,  # No key provided
            postgres_password="fallback_password",
        )

        with pytest.raises(
            ValueError, match="SECRETS_PGVECTOR_PASSWORD_KEY is required when using Google Cloud Secret Manager"
        ):
            get_postgres_password(settings)

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_vault_provider_success(self, mock_create_manager):
        """Test successful postgres password retrieval from HashiCorp Vault."""
        # Mock the secrets manager
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "vault_secret_password"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="vault",
            secrets_pgvector_password_key="postgres/password",
            vault_url="https://vault.example.com",
            vault_config='{"auth_method": "token"}',
        )

        password = get_postgres_password(settings)

        assert password == "vault_secret_password"
        mock_create_manager.assert_called_with(
            provider="vault", vault_url="https://vault.example.com", vault_config='{"auth_method": "token"}'
        )
        mock_manager.get_secret.assert_called_with("postgres/password")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_vault_provider_failure(self, mock_create_manager):
        """Test postgres password failure when Vault returns None."""
        # Mock the secrets manager to return None (failure)
        mock_manager = Mock()
        mock_manager.get_secret.return_value = None
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="vault",
            secrets_pgvector_password_key="postgres/password",
            vault_url="https://vault.example.com",
        )

        with pytest.raises(ValueError, match="Failed to retrieve postgres password from HashiCorp Vault"):
            get_postgres_password(settings)

    def test_postgres_password_vault_provider_no_key(self):
        """Test postgres password failure when Vault provider is set but no key is provided."""
        settings = Settings(
            secrets_manager="vault",
            secrets_pgvector_password_key=None,
            vault_url="https://vault.example.com",
        )

        with pytest.raises(ValueError, match="SECRETS_PGVECTOR_PASSWORD_KEY is required when using HashiCorp Vault"):
            get_postgres_password(settings)

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_password_kubernetes_provider_success(self, mock_create_manager):
        """Test successful postgres password retrieval from Kubernetes secrets."""
        # Mock the secrets manager
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "k8s_secret_password"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="kubernetes",
            secrets_pgvector_password_key="postgres-password",
            kubernetes_config='{"namespace": "default"}',
        )

        password = get_postgres_password(settings)

        assert password == "k8s_secret_password"
        mock_create_manager.assert_called_with(provider="kubernetes", kubernetes_config='{"namespace": "default"}')
        mock_manager.get_secret.assert_called_with("postgres-password")

    def test_postgres_password_kubernetes_provider_no_key(self):
        """Test postgres password failure when Kubernetes provider is set but no key is provided."""
        settings = Settings(
            secrets_manager="kubernetes",
            secrets_pgvector_password_key=None,
            kubernetes_config='{"namespace": "default"}',
        )

        with pytest.raises(ValueError, match="SECRETS_PGVECTOR_PASSWORD_KEY is required when using Kubernetes secrets"):
            get_postgres_password(settings)

    def test_postgres_password_unsupported_provider(self):
        """Test postgres password failure with unsupported secrets manager."""
        settings = Settings()
        settings.secrets_manager = "unsupported_provider"  # Set invalid provider

        with pytest.raises(ValueError, match="Unsupported secrets manager: unsupported_provider"):
            get_postgres_password(settings)

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_broker_password_google_provider_success(self, mock_create_manager):
        """Test successful broker password retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "broker_secret"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_broker_password_key="broker-password",
            google_cloud_project="test-project",
        )

        password = get_broker_password(settings)
        assert password == "broker_secret"
        mock_manager.get_secret.assert_called_with("broker-password")

    def test_broker_password_missing_key_for_external_manager(self):
        """Test broker password requires a secret key when using external secrets."""
        settings = Settings(secrets_manager="vault", secrets_broker_password_key=None)

        with pytest.raises(ValueError, match="SECRETS_BROKER_PASSWORD_KEY is required when using HashiCorp Vault"):
            get_broker_password(settings)

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_openai_api_key_google_provider_success(self, mock_create_manager):
        """Test successful OpenAI key retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "openai_secret"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_openai_api_key="openai-key",
            google_cloud_project="test-project",
        )

        api_key = get_openai_api_key(settings)
        assert api_key == "openai_secret"
        mock_manager.get_secret.assert_called_with("openai-key")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_azure_openai_api_key_google_provider_success(self, mock_create_manager):
        """Test successful Azure OpenAI key retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "azure_openai_secret"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_azure_openai_api_key="azure-openai-key",
            google_cloud_project="test-project",
        )

        api_key = get_azure_openai_api_key(settings)
        assert api_key == "azure_openai_secret"
        mock_manager.get_secret.assert_called_with("azure-openai-key")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_anthropic_api_key_google_provider_success(self, mock_create_manager):
        """Test successful Anthropic key retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "anthropic_secret"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_anthropic_api_key="anthropic-key",
            google_cloud_project="test-project",
        )

        api_key = get_anthropic_api_key(settings)
        assert api_key == "anthropic_secret"
        mock_manager.get_secret.assert_called_with("anthropic-key")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_mistral_api_key_google_provider_success(self, mock_create_manager):
        """Test successful Mistral key retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "mistral_secret"
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_mistral_api_key="mistral-key",
            google_cloud_project="test-project",
        )

        api_key = get_mistral_api_key(settings)
        assert api_key == "mistral_secret"
        mock_manager.get_secret.assert_called_with("mistral-key")

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_aws_bedrock_keys_google_provider_success(self, mock_create_manager):
        """Test successful AWS Bedrock key retrieval from Google Secret Manager."""
        mock_manager = Mock()
        mock_manager.get_secret.side_effect = ["aws_access", "aws_secret"]
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_aws_bedrock_access_key="aws-access-key",
            secrets_aws_bedrock_secret_key="aws-secret-key",
            google_cloud_project="test-project",
        )

        access_key = get_aws_bedrock_access_key(settings)
        secret_key = get_aws_bedrock_secret_key(settings)

        assert access_key == "aws_access"
        assert secret_key == "aws_secret"
        assert mock_manager.get_secret.call_args_list[0].args[0] == "aws-access-key"
        assert mock_manager.get_secret.call_args_list[1].args[0] == "aws-secret-key"

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_model_post_init_loads_openai_key_from_secrets_manager(self, mock_create_manager):
        """Test model_post_init assigns openai_api_key when loaded from configured secret manager."""
        mock_manager = Mock()
        mock_manager.get_secret.side_effect = ["db-password", "broker-password", "openai-secret"]
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key="postgres-password",
            secrets_broker_password_key="broker-password",
            secrets_openai_api_key="openai-key",
            google_cloud_project="test-project",
            llm_provider="openai",
            postgres_connection=None,
            broker_url=None,
            broker_type="amqp",
        )

        assert settings.openai_api_key == "openai-secret"

    @patch("aviator.services.secrets.create_secrets_manager")
    def test_model_post_init_loads_azure_openai_key_from_secrets_manager(self, mock_create_manager):
        """Test model_post_init assigns azure_openai_api_key when loaded from configured secret manager."""
        mock_manager = Mock()
        mock_manager.get_secret.side_effect = ["db-password", "broker-password", "azure-openai-secret"]
        mock_create_manager.return_value = mock_manager

        settings = Settings(
            secrets_manager="google",
            secrets_pgvector_password_key="postgres-password",
            secrets_broker_password_key="broker-password",
            secrets_azure_openai_api_key="azure-openai-key",
            google_cloud_project="test-project",
            llm_provider="azure_openai",
            postgres_connection=None,
            broker_url=None,
            broker_type="amqp",
            azure_openai_instance_name="my-resource",
        )

        assert settings.azure_openai_api_key == "azure-openai-secret"

    @patch.dict("os.environ", clear=False)
    @patch("aviator.services.secrets.create_secrets_manager")
    def test_postgres_connection_string_with_google_secrets(self, mock_create_manager):
        """Test that postgres connection string uses password from Google Secret Manager."""
        # Remove POSTGRES_CONNECTION from environment to allow the test to build its own
        os.environ.pop("POSTGRES_CONNECTION", None)

        # Remove POSTGRES_CONNECTION from environment to allow the test to build its own
        os.environ.pop("POSTGRES_CONNECTION", None)

        # Mock the secrets manager
        mock_manager = Mock()
        mock_manager.get_secret.return_value = "gcp_secret_password"
        mock_create_manager.return_value = mock_manager

        # Ensure ambient env vars (e.g., POSTGRES_CONNECTION) do not override this test input.
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                secrets_manager="google",
                secrets_pgvector_password_key="postgres-password",
                google_cloud_project="test-project",
                postgres_user="testuser",
                postgres_host="testhost",
                postgres_port=5433,
                postgres_database="testdb",
                postgres_connection=None,
            )

        # The connection string should include the password from GCP
        expected_connection = "postgresql://testuser:gcp_secret_password@testhost:5433/testdb"
        assert str(settings.postgres_connection) == expected_connection

    def test_broker_url_empty_string_is_treated_as_unset(self):
        """Test empty BROKER_URL values are normalized so broker settings can rebuild the DSN."""
        with patch.dict(os.environ, {"BROKER_URL": ""}, clear=False):
            settings = Settings(
                broker_type="amqp",
                broker_user="admin",
                broker_password="admin_pass",
                broker_host="rabbitmq",
                broker_vhost="/",
            )

        assert str(settings.broker_url) == "amqp://admin:admin_pass@rabbitmq/"

    def test_secrets_manager_settings_validation(self):
        """Test that secrets manager settings are properly validated."""
        # Test with valid settings
        settings = Settings(
            secrets_manager="google", secrets_pgvector_password_key="my-secret", google_cloud_project="my-project"
        )

        assert settings.secrets_manager == "google"
        assert settings.secrets_pgvector_password_key == "my-secret"
        assert settings.google_cloud_project == "my-project"

        # Test with environment provider (default)
        settings_env = Settings()
        assert settings_env.secrets_manager == "environment"
        assert settings_env.secrets_pgvector_password_key is None
        assert settings_env.google_cloud_project is None

    def test_model_description_fields(self):
        """Test that the new settings fields have proper descriptions."""
        settings = Settings()

        # Check that the fields exist and have descriptions
        fields = settings.model_fields

        assert "secrets_manager" in fields
        assert "secrets_pgvector_password_key" in fields
        assert "google_cloud_project" in fields

        # Verify descriptions are meaningful
        assert "secrets management provider" in fields["secrets_manager"].description.lower()
        assert "secret manager" in fields["secrets_pgvector_password_key"].description.lower()
        assert "project id" in fields["google_cloud_project"].description.lower()
