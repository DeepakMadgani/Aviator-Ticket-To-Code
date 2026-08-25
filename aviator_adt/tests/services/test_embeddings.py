"""Test suite for embeddings service."""

import pytest
from langchain_aws import BedrockEmbeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_mistralai import MistralAIEmbeddings
from langchain_openai import AzureOpenAIEmbeddings, OpenAIEmbeddings

from aviator.services.embeddings import EmbeddingsRegistry
from aviator.settings import EmbeddingsProvider, LLMProvider, settings


class TestEmbeddingsRegistry:
    """Tests for EmbeddingsRegistry."""

    def test_get_embeddings_google_genai(self, monkeypatch):
        """Test getting Google GenAI embeddings."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-004")
        monkeypatch.setattr(settings, "llm_location", "us-central1")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, GoogleGenerativeAIEmbeddings)
        assert embeddings.model == "text-embedding-004"

    def test_get_embeddings_openai(self, monkeypatch):
        """Test getting OpenAI embeddings."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, OpenAIEmbeddings)

    def test_get_embeddings_openai_with_model_and_base_url(self, monkeypatch):
        """Test OpenAI embeddings with model and base_url options."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-3-large")
        monkeypatch.setattr(settings, "embeddings_base_url", "https://custom.embeddings/v1")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, OpenAIEmbeddings)

    def test_embeddings_google_genai_method(self, monkeypatch):
        """Test _embeddings_google_genai method directly."""
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-004")
        monkeypatch.setattr(settings, "llm_location", "us-central1")

        registry = EmbeddingsRegistry()
        embeddings = registry._embeddings_google_genai()

        assert isinstance(embeddings, GoogleGenerativeAIEmbeddings)
        assert embeddings.model == "text-embedding-004"
        assert embeddings.location == "us-central1"

    def test_embeddings_openai_method(self, monkeypatch):
        """Test _embeddings_openai method directly."""
        monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")

        registry = EmbeddingsRegistry()
        embeddings = registry._embeddings_openai()

        assert isinstance(embeddings, OpenAIEmbeddings)

    def test_get_embeddings_azure_openai(self, monkeypatch):
        """Test getting Azure OpenAI embeddings with explicit endpoint."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_embeddings_deployment_name", "text-embedding-ada-002")
        monkeypatch.setattr(settings, "azure_openai_endpoint", "https://test.openai.azure.com/")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-key")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, AzureOpenAIEmbeddings)
        assert embeddings.deployment == "text-embedding-ada-002"

    def test_embeddings_azure_openai_method(self, monkeypatch):
        """Test _embeddings_azure_openai method directly."""
        monkeypatch.setattr(settings, "azure_openai_embeddings_deployment_name", "custom-embedding-deployment")
        monkeypatch.setattr(settings, "azure_openai_endpoint", "https://custom.openai.azure.com/")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-azure-key")

        registry = EmbeddingsRegistry()
        embeddings = registry._embeddings_azure_openai()

        assert isinstance(embeddings, AzureOpenAIEmbeddings)
        assert embeddings.deployment == "custom-embedding-deployment"

    def test_get_embeddings_azure_openai_with_instance_name(self, monkeypatch):
        """Test getting Azure OpenAI embeddings with instance name (constructed endpoint)."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_embeddings_deployment_name", "my-embedding-deployment")
        monkeypatch.setattr(settings, "azure_openai_instance_name", "testinstance")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-key")
        # Clear explicit endpoint to test instance name construction
        monkeypatch.setattr(settings, "azure_openai_endpoint", None)

        # Check that computed endpoint is constructed correctly
        assert settings.azure_openai_computed_endpoint == "https://testinstance.openai.azure.com/"

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, AzureOpenAIEmbeddings)
        assert embeddings.deployment == "my-embedding-deployment"

    def test_get_embeddings_aws_bedrock(self, monkeypatch):
        """Test getting AWS Bedrock embeddings."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AWS_BEDROCK)
        monkeypatch.setattr(settings, "aws_bedrock_region", "us-east-1")
        monkeypatch.setattr(settings, "aws_bedrock_access_key", "AKIA_TEST")
        monkeypatch.setattr(settings, "aws_bedrock_secret_key", "SECRET_TEST")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, BedrockEmbeddings)

    def test_bedrock_embeddings_method_with_model_id(self, monkeypatch):
        """Test _embeddings_aws_bedrock method with model_id option."""
        monkeypatch.setattr(settings, "aws_bedrock_region", "us-east-1")
        monkeypatch.setattr(settings, "aws_bedrock_access_key", "AKIA_TEST")
        monkeypatch.setattr(settings, "aws_bedrock_secret_key", "SECRET_TEST")
        monkeypatch.setattr(settings, "embeddings_model", "amazon.titan-embed-text")

        registry = EmbeddingsRegistry()
        embeddings = registry._embeddings_aws_bedrock()

        assert isinstance(embeddings, BedrockEmbeddings)

    def test_embeddings_with_kwargs(self, monkeypatch):
        """Test that kwargs are accepted but not used."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-004")

        embeddings = EmbeddingsRegistry.get_embeddings(custom_param="value")

        assert isinstance(embeddings, GoogleGenerativeAIEmbeddings)

    def test_multiple_embeddings_instances(self, monkeypatch):
        """Test creating multiple embeddings instances."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-004")

        embeddings1 = EmbeddingsRegistry.get_embeddings()
        embeddings2 = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings1, GoogleGenerativeAIEmbeddings)
        assert isinstance(embeddings2, GoogleGenerativeAIEmbeddings)
        assert embeddings1 is not embeddings2

    def test_embeddings_different_providers(self, monkeypatch):
        """Test switching between different providers."""
        # First get Google GenAI
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_provider", None)  # Use fallback
        embeddings1 = EmbeddingsRegistry.get_embeddings()
        assert isinstance(embeddings1, GoogleGenerativeAIEmbeddings)

        # Then get OpenAI
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")
        embeddings2 = EmbeddingsRegistry.get_embeddings()
        assert isinstance(embeddings2, OpenAIEmbeddings)

    def test_get_embeddings_mistral(self, monkeypatch):
        """Test getting Mistral embeddings via embeddings_provider."""
        monkeypatch.setattr(settings, "embeddings_provider", EmbeddingsProvider.MISTRAL)
        monkeypatch.setattr(settings, "mistral_api_key", "test-mistral-key")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, MistralAIEmbeddings)

    def test_embeddings_mistral_method(self, monkeypatch):
        """Test _embeddings_mistral method directly."""
        monkeypatch.setattr(settings, "mistral_api_key", "test-mistral-key")
        monkeypatch.setattr(settings, "embeddings_model", "mistral-embed")

        registry = EmbeddingsRegistry()
        embeddings = registry._embeddings_mistral()

        assert isinstance(embeddings, MistralAIEmbeddings)

    def test_decoupled_providers_anthropic_chat_mistral_embeddings(self, monkeypatch):
        """Test using Anthropic for chat and Mistral for embeddings."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.ANTHROPIC)
        monkeypatch.setattr(settings, "embeddings_provider", EmbeddingsProvider.MISTRAL)
        monkeypatch.setattr(settings, "mistral_api_key", "test-mistral-key")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, MistralAIEmbeddings)

    def test_decoupled_providers_openai_chat_google_embeddings(self, monkeypatch):
        """Test using OpenAI for chat and Google for embeddings."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "embeddings_provider", EmbeddingsProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_model", "text-embedding-004")

        embeddings = EmbeddingsRegistry.get_embeddings()

        assert isinstance(embeddings, GoogleGenerativeAIEmbeddings)

    def test_anthropic_without_embeddings_provider_raises_error(self, monkeypatch):
        """Test that using Anthropic without explicit embeddings_provider raises error."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.ANTHROPIC)
        monkeypatch.setattr(settings, "embeddings_provider", None)  # No explicit embeddings provider

        with pytest.raises(
            ValueError,
            match=r"Anthropic does not provide embeddings. Please set EMBEDDINGS_PROVIDER",
        ):
            EmbeddingsRegistry.get_embeddings()

    def test_embeddings_provider_overrides_llm_provider(self, monkeypatch):
        """Test that embeddings_provider takes precedence over llm_provider."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "embeddings_provider", EmbeddingsProvider.OPENAI)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        embeddings = EmbeddingsRegistry.get_embeddings()

        # Should use OpenAI (from embeddings_provider) not Google (from llm_provider)
        assert isinstance(embeddings, OpenAIEmbeddings)
