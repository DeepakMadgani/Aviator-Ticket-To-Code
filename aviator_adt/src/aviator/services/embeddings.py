"""Embeddings Registry Service Module."""

from langchain_core.embeddings import Embeddings

from aviator.settings import EmbeddingsProvider, settings


class EmbeddingsRegistry:
    """Registry for managing multiple LLM instances."""

    def _embeddings_google_genai(self, **kwargs) -> Embeddings:  # noqa: ANN003, ARG002
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        options = {"model": settings.embeddings_model, "location": settings.llm_location}
        if settings.embeddings_task_type:
            options["task_type"] = settings.embeddings_task_type
        if settings.vector_size:
            options["output_dimensionality"] = settings.vector_size
        return GoogleGenerativeAIEmbeddings(**options)

    def _embeddings_openai(self, **kwargs) -> Embeddings:  # noqa: ANN003, ARG002
        from langchain_openai import OpenAIEmbeddings

        options = {"api_key": settings.openai_api_key}

        # Add custom model if provided
        if settings.embeddings_model:
            options["model"] = settings.embeddings_model

        # Add custom base_url if provided (for OpenAI-compatible endpoints)
        if settings.embeddings_base_url:
            options["base_url"] = settings.embeddings_base_url

        return OpenAIEmbeddings(**options)

    def _embeddings_azure_openai(self, **kwargs) -> Embeddings:  # noqa: ANN003, ARG002
        from langchain_openai import AzureOpenAIEmbeddings

        options = {
            "deployment": settings.azure_openai_embeddings_deployment_name,
            "azure_endpoint": settings.azure_openai_computed_endpoint,
            "api_version": settings.azure_openai_api_version,
            "api_key": settings.azure_openai_api_key,
        }

        return AzureOpenAIEmbeddings(**options)

    def _embeddings_aws_bedrock(self, **kwargs) -> Embeddings:  # noqa: ANN003, ARG002
        from langchain_aws import BedrockEmbeddings

        options = {
            "region_name": settings.aws_bedrock_region,
            "aws_access_key_id": settings.aws_bedrock_access_key,
            "aws_secret_access_key": settings.aws_bedrock_secret_key,
        }

        # Add custom model if provided
        if settings.embeddings_model:
            options["model_id"] = settings.embeddings_model

        return BedrockEmbeddings(**options)

    def _embeddings_mistral(self, **kwargs) -> Embeddings:  # noqa: ANN003, ARG002
        from langchain_mistralai import MistralAIEmbeddings

        options = {
            "api_key": settings.mistral_api_key,
        }

        # Add custom model if provided (default is mistral-embed)
        if settings.embeddings_model:
            options["model"] = settings.embeddings_model

        return MistralAIEmbeddings(**options)

    @classmethod
    def get_embeddings(cls, **kwargs) -> Embeddings:  # noqa: ANN003
        """Get an embeddings instance based on the provider.

        Uses embeddings_provider if set, otherwise falls back to llm_provider.
        This allows decoupling chat and embeddings providers.
        """

        registry = cls()

        # Use embeddings_provider if set, otherwise fallback to llm_provider
        provider = settings.embeddings_provider
        if provider is None:
            # Map LLMProvider to EmbeddingsProvider for fallback
            # Note: ANTHROPIC doesn't have embeddings, so this would fail if used
            provider_str = settings.llm_provider.value
            if provider_str == "anthropic":
                msg = (
                    "Anthropic does not provide embeddings. Please set EMBEDDINGS_PROVIDER to a different provider "
                    "(e.g., mistral, openai, azure_openai, google_genai, aws_bedrock)."
                )
                raise ValueError(msg)
            provider = EmbeddingsProvider(provider_str)

        match provider:
            case EmbeddingsProvider.GOOGLE_GENAI:
                return registry._embeddings_google_genai(**kwargs)
            case EmbeddingsProvider.OPENAI:
                return registry._embeddings_openai(**kwargs)
            case EmbeddingsProvider.AZURE_OPENAI:
                return registry._embeddings_azure_openai(**kwargs)
            case EmbeddingsProvider.AWS_BEDROCK:
                return registry._embeddings_aws_bedrock(**kwargs)
            case EmbeddingsProvider.MISTRAL:
                return registry._embeddings_mistral(**kwargs)
