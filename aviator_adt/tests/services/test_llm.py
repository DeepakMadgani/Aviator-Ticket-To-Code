"""Test suite for LLM service."""

from langchain_aws import ChatBedrockConverse
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from aviator.services.llm import LLMRegistry
from aviator.settings import LLMProvider, settings


class TestLLMRegistry:
    """Tests for LLMRegistry."""

    def test_get_llm_google_genai(self, monkeypatch):
        """Test getting Google GenAI LLM."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")
        monkeypatch.setattr(settings, "llm_temperature", 0.7)
        monkeypatch.setattr(settings, "max_tokens", 1000)
        monkeypatch.setattr(settings, "top_k", 40)
        monkeypatch.setattr(settings, "top_p", 0.95)
        monkeypatch.setattr(settings, "llm_location", "us-central1")

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert llm.model == "gemini-2.5-flash-lite"
        assert llm.temperature == 0.7

    def test_get_llm_openai(self, monkeypatch):
        """Test getting OpenAI LLM."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "llm_temperature", 0.5)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-5-nano"

    def test_get_llm_openai_with_base_url(self, monkeypatch):
        """Test OpenAI LLM with custom base_url option."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "openai_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_base_url", "https://custom.api/v1")

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, ChatOpenAI)

    def test_get_llm_openai_with_custom_options(self, monkeypatch):
        """Test OpenAI LLM with custom options update."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        llm = LLMRegistry.get_llm(options={"temperature": 0.2, "max_tokens": 500})

        assert isinstance(llm, ChatOpenAI)
        # GPT-5 models don't support temperature parameter
        assert llm.temperature is None
        assert llm.max_tokens == 500

    def test_get_llm_assistant_model(self, monkeypatch):
        """Test getting assistant LLM model."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")
        monkeypatch.setattr(settings, "llm_model_assistant", "gemini-2.5-flash")

        llm = LLMRegistry.get_llm(assistant=True)

        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert llm.model == "gemini-2.5-flash"

    def test_get_llm_with_custom_options(self, monkeypatch):
        """Test getting LLM with custom options."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")

        llm = LLMRegistry.get_llm(options={"temperature": 0.9, "max_tokens": 2000})

        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert llm.temperature == 0.9
        assert llm.max_output_tokens == 2000

    def test_llm_google_genai_streaming(self, monkeypatch):
        """Test that streaming is enabled for Google GenAI."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")

        llm = LLMRegistry.get_llm()

        assert llm.streaming is True

    def test_llm_openai_streaming(self, monkeypatch):
        """Test that streaming is enabled for OpenAI."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        llm = LLMRegistry.get_llm()

        assert llm.streaming is True

    def test_get_llm_azure_openai(self, monkeypatch):
        """Test getting Azure OpenAI LLM with explicit endpoint. Used a non GPT 5 model to test temperature."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_deployment_name", "gpt-4o-nano")
        monkeypatch.setattr(settings, "azure_openai_endpoint", "https://test.openai.azure.com/")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_temperature", 0.7)

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, AzureChatOpenAI)
        assert llm.deployment_name == "gpt-4o-nano"
        assert llm.temperature == 0.7

    def test_get_llm_azure_openai_gpt5_no_temperature(self, monkeypatch):
        """Test Azure OpenAI LLM with GPT-5 deployment (no temperature)."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_deployment_name", "gpt-5-nano")
        monkeypatch.setattr(settings, "azure_openai_endpoint", "https://test.openai.azure.com/")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_temperature", 0.7)

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, AzureChatOpenAI)
        assert llm.deployment_name == "gpt-5-nano"
        # GPT-5 deployments should not have temperature set
        assert llm.temperature is None

    def test_get_llm_azure_openai_with_instance_name(self, monkeypatch):
        """Test getting Azure OpenAI LLM with instance name (constructed endpoint).  Used a non GPT 5 model to test temperature."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_deployment_name", "gpt-4o-nano")
        monkeypatch.setattr(settings, "azure_openai_instance_name", "myresource")
        monkeypatch.setattr(settings, "azure_openai_api_key", "test-key")
        monkeypatch.setattr(settings, "llm_temperature", 0.5)
        # Clear explicit endpoint to test instance name construction
        monkeypatch.setattr(settings, "azure_openai_endpoint", None)

        # Check that computed endpoint is constructed correctly
        assert settings.azure_openai_computed_endpoint == "https://myresource.openai.azure.com/"

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, AzureChatOpenAI)
        assert llm.deployment_name == "gpt-4o-nano"
        assert llm.temperature == 0.5

    def test_get_model_azure_openai_with_provider(self, monkeypatch):
        """Test getting Azure OpenAI model name with provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_deployment_name_assistant", "gpt-5-nano")

        model = LLMRegistry.get_model(with_provider=True)

        assert model == "azure_openai:gpt-5-nano"

    def test_get_model_azure_openai_without_provider(self, monkeypatch):
        """Test getting Azure OpenAI model name without provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AZURE_OPENAI)
        monkeypatch.setattr(settings, "azure_openai_deployment_name_assistant", "gpt-5-nano")

        model = LLMRegistry.get_model(with_provider=False)

        assert model == "gpt-5-nano"

    def test_get_model_google_genai_with_provider(self, monkeypatch):
        """Test getting model name with provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model_assistant", "gemini-2.5-flash")

        model = LLMRegistry.get_model(with_provider=True)

        assert model == "google_genai:gemini-2.5-flash"

    def test_get_model_google_genai_without_provider(self, monkeypatch):
        """Test getting model name without provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model_assistant", "gemini-2.5-flash")

        model = LLMRegistry.get_model(with_provider=False)

        assert model == "gemini-2.5-flash"

    def test_get_model_openai_with_provider(self, monkeypatch):
        """Test getting OpenAI model name with provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model_assistant", "gpt-5-nano")

        model = LLMRegistry.get_model(with_provider=True)

        assert model == "openai:gpt-5-nano"

    def test_get_model_openai_without_provider(self, monkeypatch):
        """Test getting OpenAI model name without provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model_assistant", "gpt-5-nano")

        model = LLMRegistry.get_model(with_provider=False)

        assert model == "gpt-5-nano"

    def test_multiple_llm_instances(self, monkeypatch):
        """Test creating multiple LLM instances."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")

        llm1 = LLMRegistry.get_llm()
        llm2 = LLMRegistry.get_llm()

        assert isinstance(llm1, ChatGoogleGenerativeAI)
        assert isinstance(llm2, ChatGoogleGenerativeAI)
        assert llm1 is not llm2

    def test_assistant_vs_base_model(self, monkeypatch):
        """Test difference between base and assistant models."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")
        monkeypatch.setattr(settings, "llm_model_assistant", "gemini-2.5-flash")

        base_llm = LLMRegistry.get_llm(assistant=False)
        assistant_llm = LLMRegistry.get_llm(assistant=True)

        assert base_llm.model == "gemini-2.5-flash-lite"
        assert assistant_llm.model == "gemini-2.5-flash"

    def test_llm_google_genai_all_parameters(self, monkeypatch):
        """Test that all parameters are set correctly for Google GenAI."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GOOGLE_GENAI)
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")
        monkeypatch.setattr(settings, "llm_temperature", 0.8)
        monkeypatch.setattr(settings, "max_tokens", 1500)
        monkeypatch.setattr(settings, "top_k", 50)
        monkeypatch.setattr(settings, "top_p", 0.9)
        monkeypatch.setattr(settings, "llm_location", "europe-west1")

        llm = LLMRegistry.get_llm()

        assert llm.model == "gemini-2.5-flash-lite"
        assert llm.temperature == 0.8
        assert llm.max_output_tokens == 1500
        assert llm.top_k == 50
        assert llm.top_p == 0.9
        assert llm.location == "europe-west1"

    def test_llm_openai_all_parameters(self, monkeypatch):
        """Test that all parameters are set correctly for OpenAI."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.OPENAI)
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "llm_temperature", 0.3)
        monkeypatch.setattr(settings, "max_tokens", 800)
        monkeypatch.setattr(settings, "top_p", 0.85)
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        llm = LLMRegistry.get_llm()

        assert llm.model_name == "gpt-5-nano"
        # GPT-5 models don't support temperature and top_p parameters
        assert llm.temperature is None
        assert llm.max_tokens == 800
        assert llm.top_p is None

    def test_registry_instance_method_google_genai(self, monkeypatch):
        """Test _llm_google_genai instance method."""
        monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash-lite")
        monkeypatch.setattr(settings, "llm_temperature", 0.7)

        registry = LLMRegistry()
        llm = registry._llm_google_genai()

        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert llm.model == "gemini-2.5-flash-lite"

    def test_registry_instance_method_openai(self, monkeypatch):
        """Test _llm_openai instance method."""
        monkeypatch.setattr(settings, "llm_model", "gpt-5-nano")
        monkeypatch.setattr(settings, "openai_api_key", "test-key")

        registry = LLMRegistry()
        llm = registry._llm_openai()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-5-nano"

    def test_get_llm_aws_bedrock(self, monkeypatch):
        """Test getting AWS Bedrock LLM."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AWS_BEDROCK)
        monkeypatch.setattr(settings, "llm_model", "amazon.titan-text")
        monkeypatch.setattr(settings, "aws_bedrock_region", "us-east-1")
        monkeypatch.setattr(settings, "aws_bedrock_access_key", "AKIA_TEST")
        monkeypatch.setattr(settings, "aws_bedrock_secret_key", "SECRET_TEST")

        llm = LLMRegistry.get_llm()

        assert isinstance(llm, ChatBedrockConverse)

    def test_get_llm_aws_bedrock_with_custom_options(self, monkeypatch):
        """Test AWS Bedrock LLM with custom options update."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AWS_BEDROCK)
        monkeypatch.setattr(settings, "llm_model", "amazon.titan-text-lite")
        monkeypatch.setattr(settings, "aws_bedrock_region", "us-east-1")
        monkeypatch.setattr(settings, "aws_bedrock_access_key", "AKIA_TEST")
        monkeypatch.setattr(settings, "aws_bedrock_secret_key", "SECRET_TEST")

        llm = LLMRegistry.get_llm(options={"temperature": 0.3, "top_p": 0.8})

        assert isinstance(llm, ChatBedrockConverse)

    def test_registry_instance_method_aws_bedrock(self, monkeypatch):
        """Test _llm_aws_bedrock instance method."""
        monkeypatch.setattr(settings, "llm_model", "amazon.titan-text")
        monkeypatch.setattr(settings, "aws_bedrock_region", "us-east-1")
        monkeypatch.setattr(settings, "aws_bedrock_access_key", "AKIA_TEST")
        monkeypatch.setattr(settings, "aws_bedrock_secret_key", "SECRET_TEST")

        registry = LLMRegistry()
        llm = registry._llm_aws_bedrock()

        assert isinstance(llm, ChatBedrockConverse)

    def test_get_model_bedrock_with_provider(self, monkeypatch):
        """Test getting AWS Bedrock model name with provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AWS_BEDROCK)
        monkeypatch.setattr(settings, "llm_model_assistant", "amazon.titan-text")

        model = LLMRegistry.get_model(with_provider=True)

        assert model == "aws_bedrock:amazon.titan-text"

    def test_get_model_bedrock_without_provider(self, monkeypatch):
        """Test getting AWS Bedrock model name without provider prefix."""
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.AWS_BEDROCK)
        monkeypatch.setattr(settings, "llm_model_assistant", "amazon.titan-text")

        model = LLMRegistry.get_model(with_provider=False)

        assert model == "amazon.titan-text"
