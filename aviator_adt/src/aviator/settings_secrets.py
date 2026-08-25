"""Settings secrets management utilities."""

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from aviator.services.secrets import SecretsManager
    from aviator.settings import Settings

logger = logging.getLogger(__name__)


def _raise_secret_error(message: str, cause: Exception | None = None) -> None:
    """Abstract raise function for secret retrieval errors."""
    if cause:
        raise ValueError(message) from cause
    raise ValueError(message)


def _create_configured_secrets_manager(settings: "Settings") -> "SecretsManager":
    """Create a secrets manager for the configured provider."""
    from aviator.services.secrets import create_secrets_manager

    match settings.secrets_manager:
        case "google":
            return create_secrets_manager(provider="google", project_id=settings.google_cloud_project)
        case "vault":
            return create_secrets_manager(
                provider="vault", vault_url=settings.vault_url, vault_config=settings.vault_config
            )
        case "kubernetes":
            return create_secrets_manager(provider="kubernetes", kubernetes_config=settings.kubernetes_config)
        case _:
            msg = f"Unsupported secrets manager: {settings.secrets_manager}"
            _raise_secret_error(msg)
            msg_0 = "unreachable"
            raise AssertionError(msg_0)


def get_secret_value(
    settings: "Settings",
    *,
    value: str | None,
    secret_key: str | None,
    secret_key_env_name: str,
    env_var_name: str,
    secret_label: str,
) -> str:
    """Retrieve a configured secret value from environment or secret manager.

    Args:
        settings: The Settings instance containing configuration
        value: Current in-memory value (typically loaded from env)
        secret_key: Secret manager key/path used to fetch the value
        secret_key_env_name: Secret-key setting name used by external secret managers
        env_var_name: Environment variable name used when secrets_manager='environment'
        secret_label: Human-friendly label used in error/log messages

    Returns:
        Retrieved secret value

    Raises:
        ValueError: When retrieval fails or is not configured properly

    """
    logger.info("Retrieving secret '%s'", secret_label)

    if settings.secrets_manager == "environment":
        if not value:
            msg = f"{env_var_name} environment variable not set"
            _raise_secret_error(msg)
        return cast("str", value)

    provider_names = {
        "google": "Google Cloud Secret Manager",
        "vault": "HashiCorp Vault",
        "kubernetes": "Kubernetes secrets",
    }
    provider_name = provider_names.get(settings.secrets_manager, settings.secrets_manager)
    if settings.secrets_manager not in provider_names:
        msg = f"Unsupported secrets manager: {settings.secrets_manager}"
        _raise_secret_error(msg)

    if not secret_key:
        msg = f"{secret_key_env_name} is required when using {provider_name}"
        _raise_secret_error(msg)

    secret_key_value = cast("str", secret_key)

    try:
        secrets_manager = _create_configured_secrets_manager(settings)
        secret_value = secrets_manager.get_secret(secret_key_value)

        if secret_value is None:
            msg = f"Failed to retrieve {secret_label} from {provider_name} using key '{secret_key}'"
            _raise_secret_error(msg)

        logger.info("Successfully retrieved %s from %s", secret_label, provider_name)
        return cast("str", secret_value)
    except ValueError:
        raise
    except Exception as e:
        msg = f"Error retrieving {secret_label} from {provider_name}: {e}"
        _raise_secret_error(msg, e)

    # This should never be reached due to all branches either returning or raising
    return ""


def get_postgres_password(settings: "Settings") -> str:
    """Retrieve postgres password using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.postgres_password,
        secret_key=settings.secrets_pgvector_password_key,
        secret_key_env_name="SECRETS_PGVECTOR_PASSWORD_KEY",
        env_var_name="POSTGRES_PASSWORD",
        secret_label="postgres password",
    )


def get_broker_password(settings: "Settings") -> str:
    """Retrieve broker password using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.broker_password,
        secret_key=settings.secrets_broker_password_key,
        secret_key_env_name="SECRETS_BROKER_PASSWORD_KEY",
        env_var_name="BROKER_PASSWORD",
        secret_label="broker password",
    )


def get_openai_api_key(settings: "Settings") -> str:
    """Retrieve OpenAI API key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.openai_api_key,
        secret_key=settings.secrets_openai_api_key,
        secret_key_env_name="SECRETS_OPENAI_API_KEY",
        env_var_name="OPENAI_API_KEY",
        secret_label="openai api key",
    )


def get_azure_openai_api_key(settings: "Settings") -> str:
    """Retrieve Azure OpenAI API key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.azure_openai_api_key,
        secret_key=settings.secrets_azure_openai_api_key,
        secret_key_env_name="SECRETS_AZURE_OPENAI_API_KEY",
        env_var_name="AZURE_OPENAI_API_KEY",
        secret_label="azure openai api key",
    )


def get_anthropic_api_key(settings: "Settings") -> str:
    """Retrieve Anthropic API key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.anthropic_api_key,
        secret_key=settings.secrets_anthropic_api_key,
        secret_key_env_name="SECRETS_ANTHROPIC_API_KEY",
        env_var_name="ANTHROPIC_API_KEY",
        secret_label="anthropic api key",
    )


def get_mistral_api_key(settings: "Settings") -> str:
    """Retrieve Mistral API key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.mistral_api_key,
        secret_key=settings.secrets_mistral_api_key,
        secret_key_env_name="SECRETS_MISTRAL_API_KEY",
        env_var_name="MISTRAL_API_KEY",
        secret_label="mistral api key",
    )


def get_aws_bedrock_access_key(settings: "Settings") -> str:
    """Retrieve AWS Bedrock access key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.aws_bedrock_access_key,
        secret_key=settings.secrets_aws_bedrock_access_key,
        secret_key_env_name="SECRETS_AWS_BEDROCK_ACCESS_KEY",
        env_var_name="AWS_BEDROCK_ACCESS_KEY",
        secret_label="aws bedrock access key",
    )


def get_aws_bedrock_secret_key(settings: "Settings") -> str:
    """Retrieve AWS Bedrock secret key using configured secret provider."""
    return get_secret_value(
        settings,
        value=settings.aws_bedrock_secret_key,
        secret_key=settings.secrets_aws_bedrock_secret_key,
        secret_key_env_name="SECRETS_AWS_BEDROCK_SECRET_KEY",
        env_var_name="AWS_BEDROCK_SECRET_KEY",
        secret_label="aws bedrock secret key",
    )
