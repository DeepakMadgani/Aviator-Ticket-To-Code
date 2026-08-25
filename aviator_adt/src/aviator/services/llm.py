"""LLM Registry Service Module."""

import json
import logging

from langchain_core.language_models.chat_models import BaseChatModel

from aviator.settings import LLMProvider, settings

logger = logging.getLogger(__name__)


class LLMRegistry:
    """Registry for managing multiple LLM instances."""

    @staticmethod
    def _parse_safety_settings() -> dict | None:
        """Parse the LLM_SAFETY_SETTINGS JSON string into a dict[HarmCategory, HarmBlockThreshold].

        Returns None if safety settings are empty or cannot be parsed.
        """
        from langchain_google_genai import HarmBlockThreshold, HarmCategory

        try:
            raw = json.loads(settings.llm_safety_settings)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse llm_safety_settings JSON; safety settings will not be applied.")
            return None

        result: dict = {}
        for entry in raw:
            try:
                category = HarmCategory[entry["category"]]
                threshold = HarmBlockThreshold[entry["threshold"]]
                result[category] = threshold
            except KeyError:
                logger.warning("Unknown HarmCategory or HarmBlockThreshold value in llm_safety_settings: %s", entry)
        return result or None

    def _llm_google_genai(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_google_genai import ChatGoogleGenerativeAI

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_google_genai/ChatGoogleGenerativeAI/
        options = {
            "model": settings.llm_model_assistant if kwargs.get("assistant") else settings.llm_model,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.max_tokens,
            "top_k": settings.top_k,
            "top_p": settings.top_p,
            "streaming": True,
            "safety_settings": self._parse_safety_settings(),
            "request_timeout": 600,
        }

        if settings.llm_location:
            # langchain-google-genai v4+ requires vertexai=True to use Vertex AI
            # endpoint. Setting only location is not sufficient.
            options["location"] = settings.llm_location
            options["vertexai"] = True

        if "options" in kwargs:
            options.update(kwargs["options"])

        return ChatGoogleGenerativeAI(**options)

    def _llm_openai(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_openai import ChatOpenAI

        # Get model name for special handling
        model_name = settings.llm_model_assistant if kwargs.get("assistant") else settings.llm_model

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_openai/ChatOpenAI/
        options = {
            "model": model_name,
            "max_tokens": settings.max_tokens,
            "api_key": settings.openai_api_key,
            "streaming": True,
        }

        # GPT-5 models may not support temperature and top_p parameters
        # Only add these parameters if it's not a GPT-5 model
        if not (model_name and "gpt-5" in model_name):
            options["temperature"] = settings.llm_temperature
            options["top_p"] = settings.top_p

        # Add custom base_url if provided (for OpenAI-compatible endpoints)
        if settings.llm_base_url:
            options["base_url"] = settings.llm_base_url

        # Apply custom options AFTER setting defaults
        if "options" in kwargs:
            # For GPT-5 models, exclude temperature and top_p from custom options
            if model_name and "gpt-5" in model_name:
                custom_options = {k: v for k, v in kwargs["options"].items() if k not in ("temperature", "top_p")}
                options.update(custom_options)
            else:
                options.update(kwargs["options"])

        return ChatOpenAI(**options)

    def _llm_azure_openai(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_openai import AzureChatOpenAI

        # Get deployment name based on whether it's assistant model or base model
        deployment_name = (
            settings.azure_openai_deployment_name_assistant
            if kwargs.get("assistant")
            else settings.azure_openai_deployment_name
        )

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_openai/AzureChatOpenAI/
        options = {
            "deployment_name": deployment_name,
            "azure_endpoint": settings.azure_openai_computed_endpoint,
            "api_version": settings.azure_openai_api_version,
            "api_key": settings.azure_openai_api_key,
            "max_tokens": settings.max_tokens,
            "streaming": True,
        }

        # Azure OpenAI models support temperature and top_p for non-GPT-5 models
        # Check deployment name for GPT-5 indicators (common naming: gpt-5, gpt5, etc.)
        if not (deployment_name and any(indicator in deployment_name.lower() for indicator in ["gpt-5", "gpt5"])):
            options["temperature"] = settings.llm_temperature
            options["top_p"] = settings.top_p

        # Apply custom options AFTER setting defaults
        if "options" in kwargs:
            # For GPT-5 deployments, exclude temperature and top_p from custom options
            if deployment_name and any(indicator in deployment_name.lower() for indicator in ["gpt-5", "gpt5"]):
                custom_options = {k: v for k, v in kwargs["options"].items() if k not in ("temperature", "top_p")}
                options.update(custom_options)
            else:
                options.update(kwargs["options"])

        return AzureChatOpenAI(**options)

    def _llm_aws_bedrock(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_aws import ChatBedrockConverse

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_aws/

        options = {
            "model": settings.llm_model_assistant if kwargs.get("assistant") else settings.llm_model,
            "region_name": settings.aws_bedrock_region,
            "temperature": settings.llm_temperature,
            "top_p": settings.top_p,
            "aws_access_key_id": settings.aws_bedrock_access_key,
            "aws_secret_access_key": settings.aws_bedrock_secret_key,
        }

        if "options" in kwargs:
            options.update(kwargs["options"])

        return ChatBedrockConverse(**options)

    def _llm_anthropic(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_anthropic import ChatAnthropic

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_anthropic/
        options = {
            "model": settings.llm_model_assistant if kwargs.get("assistant") else settings.llm_model,
            "temperature": settings.llm_temperature,
            "top_k": settings.top_k,
            "max_tokens": settings.max_tokens,
            "api_key": settings.anthropic_api_key,
            "streaming": True,
        }

        # Add custom base_url if provided (for Anthropic-compatible endpoints)
        if settings.llm_base_url:
            options["base_url"] = settings.llm_base_url

        if "options" in kwargs:
            options.update(kwargs["options"])

        return ChatAnthropic(**options)

    def _llm_mistral(self, **kwargs) -> BaseChatModel:  # noqa: ANN003
        from langchain_mistralai import ChatMistralAI

        # Full list of options: https://reference.langchain.com/python/integrations/langchain_mistralai/
        options = {
            "model": settings.llm_model_assistant if kwargs.get("assistant") else settings.llm_model,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
            "mistral_api_key": settings.mistral_api_key,
            "streaming": True,
        }

        # Add custom base_url if provided (for Mistral-compatible endpoints)
        if settings.llm_base_url:
            options["base_url"] = settings.llm_base_url

        if "options" in kwargs:
            options.update(kwargs["options"])

        return ChatMistralAI(**options)

    @classmethod
    def get_llm(cls, **kwargs) -> BaseChatModel:  # noqa: ANN003
        """Get an LLM instance based on the provider."""

        registry = cls()
        match settings.llm_provider:
            case LLMProvider.GOOGLE_GENAI:
                return registry._llm_google_genai(**kwargs)
            case LLMProvider.OPENAI:
                return registry._llm_openai(**kwargs)
            case LLMProvider.AZURE_OPENAI:
                return registry._llm_azure_openai(**kwargs)
            case LLMProvider.AWS_BEDROCK:
                return registry._llm_aws_bedrock(**kwargs)
            case LLMProvider.ANTHROPIC:
                return registry._llm_anthropic(**kwargs)
            case LLMProvider.MISTRAL:
                return registry._llm_mistral(**kwargs)

    @classmethod
    def get_model(cls, with_provider: bool = True, **kwargs) -> str:  # noqa: ANN003, ARG003
        """Get the model name based on the provider."""

        match settings.llm_provider:
            case LLMProvider.GOOGLE_GENAI:
                return "google_genai:" + settings.llm_model_assistant if with_provider else settings.llm_model_assistant

            case LLMProvider.OPENAI:
                return "openai:" + settings.llm_model_assistant if with_provider else settings.llm_model_assistant

            case LLMProvider.AZURE_OPENAI:
                deployment_name = settings.azure_openai_deployment_name_assistant
                return "azure_openai:" + deployment_name if with_provider else deployment_name

            case LLMProvider.AWS_BEDROCK:
                return "aws_bedrock:" + settings.llm_model_assistant if with_provider else settings.llm_model_assistant

            case LLMProvider.ANTHROPIC:
                return "anthropic:" + settings.llm_model_assistant if with_provider else settings.llm_model_assistant

            case LLMProvider.MISTRAL:
                return "mistral:" + settings.llm_model_assistant if with_provider else settings.llm_model_assistant
